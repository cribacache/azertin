"""Portal de administración (staff): roles de usuario, backlog de preguntas
y eventos de seguridad.

Vive dentro de la app, detrás del login de Google (no está en
`chat.middleware.EXENTAS`) y además exige `is_staff`. Separado del `/admin/`
de Django a propósito: acá se trabaja el día a día de quién puede preguntar
qué, con la vista cruzada contra BUK que el admin genérico no da.
"""

import functools

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.http import HttpResponseForbidden
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from . import buk
from .models import ConsultaNoResuelta, EventoSeguridad, InvitacionRol, PerfilUsuario

User = get_user_model()


def _solo_staff(vista):
    @functools.wraps(vista)
    def envoltura(request, *args, **kwargs):
        if not (request.user.is_authenticated and request.user.is_staff):
            return HttpResponseForbidden("Necesitas ser staff para entrar al portal.")
        return vista(request, *args, **kwargs)
    return envoltura


def _directorio_por_email():
    """{email: registro} de BUK, para cruzar cada cuenta con su empleado.
    Vacío si BUK no responde: el portal igual funciona, sin la columna."""
    try:
        directorio, _ = buk.directorio()
    except buk.BukError:
        return {}, {}
    por_email = {p["email"]: p for p in directorio.values() if p.get("email")}
    return por_email, directorio


# Subir a "gerencia" (acceso total) queda reservado a superusuarios: un staff
# comun administra ejecutivo/sin_acceso, no reparte god-mode.
_ERROR_GERENCIA = "Solo un superusuario puede asignar el rol gerencia."


def _gerencia_sin_permiso(request, rol):
    return rol == PerfilUsuario.GERENCIA and not request.user.is_superuser


def _cambiar_rol(request):
    uid = request.POST.get("usuario_id")
    rol = request.POST.get("rol")
    objetivo = User.objects.filter(pk=uid).first()
    if _gerencia_sin_permiso(request, rol):
        return HttpResponseForbidden(_ERROR_GERENCIA)
    if objetivo and rol in dict(PerfilUsuario.ROLES):
        perfil, _ = PerfilUsuario.objects.get_or_create(usuario=objetivo)
        perfil.rol = rol
        perfil.actualizado_por = request.user
        perfil.save(update_fields=["rol", "actualizado_por", "actualizado_en"])
    return redirect("portal-usuarios")


def _invitar(request):
    """Deja un rol listo de antemano para un correo del dominio, sin esperar
    a que esa persona inicie sesion por primera vez. Si ya existe una cuenta
    con ese correo (ya inicio sesion alguna vez), el rol se aplica directo en
    vez de dejarlo pendiente -no tiene sentido esperar un "primer login" que
    ya paso."""
    email = (request.POST.get("email") or "").strip().lower()
    rol = request.POST.get("rol")
    dominio = f"@{settings.GOOGLE_WORKSPACE_DOMAIN.lower()}"

    if _gerencia_sin_permiso(request, rol):
        return HttpResponseForbidden(_ERROR_GERENCIA)
    if rol not in dict(PerfilUsuario.ROLES):
        messages.error(request, "Rol invalido.")
        return redirect("portal-usuarios")
    if not email or not email.endswith(dominio):
        messages.error(request, f"El correo tiene que ser del dominio {dominio}.")
        return redirect("portal-usuarios")

    existente = User.objects.filter(email__iexact=email).first()
    if existente:
        perfil, _ = PerfilUsuario.objects.get_or_create(usuario=existente)
        perfil.rol = rol
        perfil.actualizado_por = request.user
        perfil.save(update_fields=["rol", "actualizado_por", "actualizado_en"])
        messages.success(request, f"{email} ya tenía cuenta: se le asignó el rol directo.")
    else:
        InvitacionRol.objects.update_or_create(
            email=email, defaults={"rol": rol, "creada_por": request.user})
        messages.success(
            request, f"Listo: {email} va a entrar con ese rol apenas inicie sesión.")
    return redirect("portal-usuarios")


def _cancelar_invitacion(request):
    InvitacionRol.objects.filter(pk=request.POST.get("invitacion_id")).delete()
    return redirect("portal-usuarios")


_ACCIONES_POST = {
    "invitar": _invitar,
    "cancelar_invitacion": _cancelar_invitacion,
}


@never_cache
@_solo_staff
@require_http_methods(["GET", "POST"])
def usuarios(request):
    if request.method == "POST":
        accion = _ACCIONES_POST.get(request.POST.get("accion"), _cambiar_rol)
        return accion(request)

    por_email, directorio = _directorio_por_email()
    perfiles = {p.usuario_id: p for p in PerfilUsuario.objects.all()}

    es_super = request.user.is_superuser
    filas = []
    for u in User.objects.order_by("email", "username"):
        perfil = perfiles.get(u.pk)
        emp = None
        if perfil and perfil.buk_employee_id:
            emp = directorio.get(perfil.buk_employee_id)
        if emp is None:
            emp = por_email.get((u.email or u.get_username()).strip().lower())
        rol = perfil.rol if perfil else PerfilUsuario.ROL_DEFECTO
        filas.append({
            "usuario": u,
            "rol": rol,
            "rol_explicito": perfil is not None,
            "es_superuser": u.is_superuser,
            "empleado": emp,
            # No editable desde acá: superusuarios (rol fijo) o filas en
            # gerencia cuando quien mira no es superusuario.
            "bloqueado": u.is_superuser or (rol == PerfilUsuario.GERENCIA and not es_super),
        })

    # Un staff no superusuario no puede siquiera elegir "gerencia" en el select.
    roles = [r for r in PerfilUsuario.ROLES
             if es_super or r[0] != PerfilUsuario.GERENCIA]

    return render(request, "portal/usuarios.html", {
        "filas": filas,
        "roles": roles,
        "buk_ok": bool(directorio),
        "rol_defecto": PerfilUsuario.ROL_DEFECTO,
        "invitaciones": InvitacionRol.objects.all(),
        "dominio": settings.GOOGLE_WORKSPACE_DOMAIN,
    })


@never_cache
@_solo_staff
@require_http_methods(["GET"])
def eventos(request):
    tipo = request.GET.get("tipo") or ""
    qs = EventoSeguridad.objects.select_related("usuario")
    if tipo in dict(EventoSeguridad.TIPOS):
        qs = qs.filter(tipo=tipo)
    return render(request, "portal/eventos.html", {
        "eventos": qs[:200],
        "tipos": EventoSeguridad.TIPOS,
        "tipo_activo": tipo,
    })


# "pendientes" es el default: es lo que hay que programar, lo que ya se
# cubrio no compite por la atencion de quien entra a mirar el backlog.
_ESTADOS_PREGUNTAS = {
    "pendientes": {"resuelta": False, "etiqueta": "Pendientes"},
    "resueltas": {"resuelta": True, "etiqueta": "Resueltas"},
    "todas": {"resuelta": None, "etiqueta": "Todas"},
}


@never_cache
@_solo_staff
@require_http_methods(["GET", "POST"])
def preguntas(request):
    """Backlog de preguntas que el asistente no supo responder
    (`ConsultaNoResuelta`), ordenado por cuantas veces se repitio: las que
    mas se repiten son las que conviene programar primero.
    """
    if request.method == "POST":
        fila = ConsultaNoResuelta.objects.filter(
            pk=request.POST.get("consulta_id")).first()
        if fila:
            fila.resuelta = request.POST.get("accion") == "marcar_resuelta"
            fila.save(update_fields=["resuelta"])
        # Validado contra la lista fija de estados antes de armar la URL: el
        # valor viene del POST, no queda a la vista.
        vuelta = request.POST.get("estado")
        vuelta = vuelta if vuelta in _ESTADOS_PREGUNTAS else "pendientes"
        return redirect(f"{request.path}?estado={vuelta}")

    estado = request.GET.get("estado") or "pendientes"
    if estado not in _ESTADOS_PREGUNTAS:
        estado = "pendientes"
    filtro = _ESTADOS_PREGUNTAS[estado]["resuelta"]

    qs = ConsultaNoResuelta.objects.all()
    if filtro is not None:
        qs = qs.filter(resuelta=filtro)

    return render(request, "portal/preguntas.html", {
        "preguntas": qs[:200],
        "estados": _ESTADOS_PREGUNTAS,
        "estado_activo": estado,
    })
