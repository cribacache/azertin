"""Portal para que Personas cargue el turno/modalidad de cada empleado.

Vive en `/rrhh/turnos/`, aparte de `/portal/` (ese es "quien puede preguntar
que en Iris"; esto es un dato de Personas, con su propia lista de acceso).
Acceso: SOLO los correos en `settings.TURNOS_PORTAL_USUARIOS` -mismo
criterio sin excepcion de rol/superusuario que `chat/salas.py` y
`chat/finder.py`.

Guarda en `Turno` (chat/models.py), por `buk_employee_id`: el empleado sale
de BUK (chat/buk.py::directorio), no se escribe a mano, para no repetir el
problema de nombres que no calzan letra por letra que tiene la planilla que
lee `chat/turnos.py`. Esa planilla sigue intacta por ahora -conectar esto
con lo que Iris responde es un paso aparte, todavia no hecho.
"""

import functools

from django.conf import settings
from django.contrib import messages
from django.http import HttpResponseForbidden
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from . import buk
from .forms import TurnoCrearForm, TurnoEditarForm
from .models import Turno


def _solo_turnos(vista):
    @functools.wraps(vista)
    def envoltura(request, *args, **kwargs):
        correo = (getattr(request.user, "email", "") or "").strip().lower()
        if not (request.user.is_authenticated and correo in settings.TURNOS_PORTAL_USUARIOS):
            return HttpResponseForbidden(
                "No tienes acceso a este portal. Es solo para el equipo de Personas."
            )
        return vista(request, *args, **kwargs)
    return envoltura


def _directorio():
    """{id: registro} de BUK activos. Vacio (no lanza) si BUK no responde:
    el portal muestra el aviso y deja ver/editar lo ya cargado igual."""
    try:
        directorio, _ = buk.directorio()
        return directorio
    except buk.BukError:
        return {}


def _opciones_sin_turno(directorio):
    """(id, "Nombre — Cargo") de activos que todavia no tienen fila en
    Turno, para el select de alta -evita elegir a alguien dos veces (la
    columna ya es unique, pero mejor no ni mostrar la opcion)."""
    tomados = set(Turno.objects.values_list("buk_employee_id", flat=True))
    return sorted(
        (
            (str(emp["id"]), f'{emp["nombre"]} — {emp["cargo"]}' if emp["cargo"] else emp["nombre"])
            for emp in directorio.values() if emp["id"] not in tomados
        ),
        key=lambda par: par[1].lower(),
    )


def _crear(request, opciones):
    form = TurnoCrearForm(request.POST, opciones_empleado=opciones)
    if not form.is_valid():
        return form
    turno = form.save(commit=False)
    turno.buk_employee_id = int(form.cleaned_data["buk_employee_id"])
    turno.actualizado_por = request.user
    turno.save()
    messages.success(request, "Turno cargado.")
    return None


def _actualizar(request):
    turno = Turno.objects.filter(pk=request.POST.get("turno_id")).first()
    if turno is None:
        messages.error(request, "Ese turno ya no existe.")
        return
    form = TurnoEditarForm(request.POST, instance=turno)
    if form.is_valid():
        turno = form.save(commit=False)
        turno.actualizado_por = request.user
        turno.save()
        messages.success(request, "Turno actualizado.")
    else:
        detalle = " ".join(f"{c}: {', '.join(e)}" for c, e in form.errors.items())
        messages.error(request, f"No se pudo guardar: {detalle}")


def _eliminar(request):
    Turno.objects.filter(pk=request.POST.get("turno_id")).delete()
    messages.success(request, "Turno eliminado.")


@never_cache
@_solo_turnos
@require_http_methods(["GET", "POST"])
def turnos(request):
    directorio = _directorio()
    opciones = _opciones_sin_turno(directorio)
    form_crear = None

    if request.method == "POST":
        accion = request.POST.get("accion")
        if accion == "crear":
            form_crear = _crear(request, opciones)
        elif accion == "actualizar":
            _actualizar(request)
        elif accion == "eliminar":
            _eliminar(request)
        if form_crear is None:
            return redirect("turnos-portal")
        # Solo llega aca si "crear" fallo la validacion: se re-renderiza
        # abajo con los errores, en vez de perder lo que la persona tipeo.

    filas = []
    for turno in Turno.objects.all():
        empleado = directorio.get(turno.buk_employee_id)
        filas.append({
            "turno": turno,
            "form": TurnoEditarForm(instance=turno),
            "nombre": empleado["nombre"] if empleado else f"Empleado BUK #{turno.buk_employee_id}",
            "cargo": empleado["cargo"] if empleado else "",
        })
    # Agrupado por departamento para que la tabla se lea de corrido, no
    # salteada -es como esta hoy la planilla que esto reemplaza.
    filas.sort(key=lambda f: (f["turno"].get_departamento_display(), f["nombre"]))

    return render(request, "portal/turnos.html", {
        "filas": filas,
        "form_crear": form_crear or TurnoCrearForm(opciones_empleado=opciones),
        "buk_ok": bool(directorio),
        "hay_cupo": bool(opciones),
    })
