"""Qué puede ver cada rol. Esta es la barrera real, no el prompt del modelo.

Se ejecuta entre `chat/asistente.py` y `chat/herramientas.py`: cada vez que
Gemini pide una herramienta, `ejecutar()` decide si la deja correr tal cual,
si le filtra el resultado a la jerarquía de quien pregunta, o si la niega.

Reglas por rol:
- `gerencia`: todo, sin tocar.
- `ejecutivo`: información general libre (políticas, cuentas, dotación,
  cumpleaños del mes). Datos de personas concretas SOLO de su misma familia
  de rol en BUK, o de sí mismo. Los beneficios ajenos no, ni siquiera de los
  pares: solo los propios.
- `sin_acceso`: nada.

Un ejecutivo sin match en BUK (sin `employee_id` ni `familia` en el contexto)
solo se puede consultar a sí mismo, y como no tiene id, en la práctica no ve
datos de nadie.
"""

import logging

from django.conf import settings

from . import buk, personas
from .intents import normalizar
from .models import EventoSeguridad, PerfilUsuario, registrar_evento

logger = logging.getLogger(__name__)

# Herramientas que devuelven una lista de personas: se filtran a la jerarquía.
TOOLS_LISTADO = {"listar_ausencias", "quien_esta_trabajando", "equipo_de"}
# Herramientas que hablan de UNA persona nombrada: se niegan si está fuera de
# alcance, con aviso explícito (decisión del proyecto).
TOOLS_PERSONA = {"info_persona", "ausencias_de_persona", "cumpleanos_de_persona",
                 "beneficios_de_persona"}
# Resuelve a una persona por el texto de un cargo, no por nombre.
TOOLS_CARGO = {"persona_por_cargo"}

LIBRE = "libre"        # corre tal cual
PARES = "pares"        # limitado a la misma familia de rol + uno mismo
PROPIO = "propio"      # solo sobre uno mismo

MATRIZ_EJECUTIVO = {
    # Información general, sin datos de una persona concreta.
    "buscar_politica": LIBRE,
    "listar_cuentas": LIBRE,
    "listar_beneficios": LIBRE,
    "dotacion": LIBRE,
    "cumpleanos": LIBRE,          # solo día y mes, el año ya viene descartado
    # Datos de personas: acotados a la jerarquía.
    "listar_ausencias": PARES,
    "quien_esta_trabajando": PARES,
    "equipo_de": PARES,
    "info_persona": PARES,
    "ausencias_de_persona": PARES,
    "cumpleanos_de_persona": PARES,
    "persona_por_cargo": PARES,
    # Los beneficios son más sensibles que la disponibilidad: solo los propios.
    "beneficios_de_persona": PROPIO,
}

MSG_SIN_ACCESO = ("Tu cuenta no tiene acceso al asistente. Escríbele al equipo "
                  "de Personas si crees que es un error.")
MSG_FUERA_ALCANCE = ("No tienes acceso a la información de esa persona: tu perfil "
                     "solo cubre a quienes están en tu misma línea.")


def _directorio():
    try:
        directorio, _ = buk.directorio()
        return directorio
    except buk.BukError:
        return None


def _ids_permitidos(ctx, directorio, regla):
    """Ids de empleado que este contexto puede consultar bajo `regla`."""
    yo = {ctx.employee_id} if ctx.employee_id else set()
    if regla == PROPIO:
        return yo
    if not ctx.familia:
        return yo
    pares = {pid for pid, p in directorio.items()
             if (p.get("familia") or "").strip() == ctx.familia}
    return pares | yo


def _resolver_objetivo(nombre_tool, argumentos, directorio):
    """Ids de empleado a los que apunta la llamada, o set vacío si no resuelve
    a nadie (nombre inexistente): en ese caso se deja correr la herramienta,
    que ya responde "no encontrada" sin filtrar nada sensible."""
    if nombre_tool in TOOLS_CARGO:
        cargo = normalizar(str(argumentos.get("cargo") or ""))
        palabras = {p for p in cargo.split() if len(p) >= 3}
        if not palabras:
            return set()
        return {pid for pid, p in directorio.items()
                if palabras <= set(normalizar(p.get("cargo") or "").split())}
    nombre = str(argumentos.get("nombre") or "")
    ids, _ = personas.buscar(nombre, directorio)
    return set(ids)


def _filtrar_listado(resultado, nombres_ok):
    if not isinstance(resultado, dict) or not isinstance(resultado.get("personas"), list):
        return resultado
    visibles = [p for p in resultado["personas"] if p.get("nombre") in nombres_ok]
    salida = dict(resultado)
    salida["personas"] = visibles
    # El modelo no debe reportar totales que abarcan gente que no puede ver.
    salida["alcance_limitado"] = True
    if "total" in salida:
        salida["total"] = len(visibles)
    if "truncado" in salida:
        salida["truncado"] = False
    if "trabajando" in salida:
        salida["trabajando"] = len(visibles)
    salida.pop("fuera", None)
    return salida


def _denegar(ctx, nombre_tool, motivo):
    registrar_evento(
        EventoSeguridad.AUTZ_DENEGADA,
        usuario=getattr(ctx, "usuario", None),
        detalle=f"{ctx.rol} intentó {nombre_tool}",
    )
    return {"autorizado": False, "motivo": motivo}


def ejecutar(ctx, nombre_tool, argumentos, fn):
    """Corre `fn` (la herramienta) aplicando la política del rol de `ctx`.

    `ctx` es un `chat.perfil.Contexto`; `fn` es un callable sin argumentos que
    ejecuta la herramienta ya con sus parámetros. Devuelve el resultado de la
    herramienta, una versión filtrada, o `{"autorizado": false, ...}`.
    """
    if not settings.AUTORIZACION_ACTIVA or ctx is None:
        return fn()
    if ctx.rol == PerfilUsuario.GERENCIA:
        return fn()
    if ctx.rol == PerfilUsuario.SIN_ACCESO:
        return _denegar(ctx, nombre_tool, MSG_SIN_ACCESO)

    regla = MATRIZ_EJECUTIVO.get(nombre_tool)
    if regla is None:                       # herramienta nueva sin clasificar
        return _denegar(ctx, nombre_tool, MSG_SIN_ACCESO)
    if regla == LIBRE:
        return fn()

    directorio = _directorio()
    if directorio is None:
        # BUK caído: no se puede verificar el alcance y la herramienta va a
        # fallar igual con su propio error. No se inventa una negación.
        return fn()

    permitidos = _ids_permitidos(ctx, directorio, regla)

    if nombre_tool in TOOLS_LISTADO:
        nombres_ok = {directorio[i]["nombre"] for i in permitidos if i in directorio}
        return _filtrar_listado(fn(), nombres_ok)

    objetivo = _resolver_objetivo(nombre_tool, argumentos, directorio)
    if not objetivo or objetivo <= permitidos:
        return fn()
    return _denegar(ctx, nombre_tool, MSG_FUERA_ALCANCE)
