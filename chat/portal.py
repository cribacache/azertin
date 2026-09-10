"""Portal de administración (staff): roles de usuario y eventos de seguridad.

Vive dentro de la app, detrás del login de Google (no está en
`chat.middleware.EXENTAS`) y además exige `is_staff`. Separado del `/admin/`
de Django a propósito: acá se trabaja el día a día de quién puede preguntar
qué, con la vista cruzada contra BUK que el admin genérico no da.
"""

import functools

from django.contrib.auth import get_user_model
from django.http import HttpResponseForbidden
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from . import buk
from .models import EventoSeguridad, PerfilUsuario

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


@_solo_staff
@require_http_methods(["GET", "POST"])
def usuarios(request):
    if request.method == "POST":
        uid = request.POST.get("usuario_id")
        rol = request.POST.get("rol")
        objetivo = User.objects.filter(pk=uid).first()
        # Subir a "gerencia" (acceso total) queda reservado a superusuarios: un
        # staff comun administra ejecutivo/sin_acceso, no reparte god-mode.
        if rol == PerfilUsuario.GERENCIA and not request.user.is_superuser:
            return HttpResponseForbidden(
                "Solo un superusuario puede asignar el rol gerencia.")
        if objetivo and rol in dict(PerfilUsuario.ROLES):
            perfil, _ = PerfilUsuario.objects.get_or_create(usuario=objetivo)
            perfil.rol = rol
            perfil.actualizado_por = request.user
            perfil.save(update_fields=["rol", "actualizado_por", "actualizado_en"])
        return redirect("portal-usuarios")

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
    })


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
