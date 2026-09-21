"""Quién es la persona que está preguntando.

Cruza la cuenta de Google con la que inició sesión (`user.email`) contra el
empleado de BUK que tiene ese correo, y arma un `Contexto` con lo que necesita
`chat/autorizacion.py` para decidir qué puede ver (el rol y la familia de rol,
la "jerarquía", de quien pregunta) y lo que necesita `chat/asistente.py` para
dirigirse a esa persona por su nombre (`nombre_pila`).

Si no hay match en BUK (contratista, cuenta de servicio, correo que no calza),
el contexto queda sin `employee_id`, `familia` ni `nombre_pila`:
`autorizacion.py` lo trata como un ejecutivo que solo se puede consultar a sí
mismo, y el asistente no saluda por nombre a nadie.
"""

import logging
from dataclasses import dataclass, field

from . import buk
from .models import PerfilUsuario, rol_de

logger = logging.getLogger(__name__)


@dataclass
class Contexto:
    rol: str = PerfilUsuario.SIN_ACCESO
    employee_id: int | None = None
    nombre: str = ""
    nombre_pila: str = ""  # para saludar y dirigirse a la persona (ver asistente.py)
    familia: str = ""
    area: str = ""
    cuentas: list = field(default_factory=list)
    usuario: object = None  # el User, solo para registrar eventos de seguridad

    @property
    def es_gerencia(self):
        return self.rol == PerfilUsuario.GERENCIA


def _nombre_para_saludar(empleado):
    """Nombre corto para dirigirse a la persona: su apodo si tiene uno
    registrado en BUK (asi la conoce el equipo), si no su primer nombre."""
    apodo = (empleado.get("apodo") or "").strip()
    if apodo:
        return apodo
    pila = (empleado.get("_nombre_pila") or "").strip()
    if pila:
        return pila.split()[0]
    nombre = (empleado.get("nombre") or "").strip()
    return nombre.split()[0] if nombre else ""


def _empleado_por_email(email, directorio):
    if not email:
        return None
    email = email.strip().lower()
    for persona in directorio.values():
        if persona.get("email") and persona["email"] == email:
            return persona
    return None


def empleado_de(usuario):
    """Registro de directorio del empleado BUK que corresponde a `usuario`, o
    None. Usa el override `buk_employee_id` del perfil si está puesto; si no,
    cruza por email. Nunca lanza: si BUK está caído, devuelve None."""
    if usuario is None or not getattr(usuario, "is_authenticated", False):
        return None
    try:
        directorio, _ = buk.directorio()
    except buk.BukError as error:
        logger.warning("no se pudo resolver el empleado de %s: %s", usuario, error)
        return None

    perfil = PerfilUsuario.objects.filter(usuario=usuario).first()
    if perfil and perfil.buk_employee_id:
        return directorio.get(perfil.buk_employee_id)
    return _empleado_por_email(usuario.email or usuario.get_username(), directorio)


def contexto(usuario):
    """Contexto de autorización de quien pregunta. Nunca lanza."""
    rol = rol_de(usuario)
    empleado = empleado_de(usuario)
    if empleado is None:
        return Contexto(rol=rol, usuario=usuario)
    return Contexto(
        rol=rol,
        employee_id=empleado.get("id"),
        nombre=empleado.get("nombre", ""),
        nombre_pila=_nombre_para_saludar(empleado),
        familia=(empleado.get("familia") or "").strip(),
        area=(empleado.get("area") or "").strip(),
        cuentas=list(empleado.get("cuentas") or []),
        usuario=usuario,
    )


def ambito_cache(ctx):
    """Segmento para la llave del caché de respuestas.

    Dos personas ven la MISMA respuesta a la misma pregunta solo si comparten
    lo que el filtro de autorización les deja ver: gerencia con gerencia, y
    ejecutivos entre sí solo dentro de la misma familia de rol. Sin esto, un
    ejecutivo podría recibir del caché la respuesta completa de un gerente.

    Quien tiene salas y agenda de reuniones habilitadas (lista de correos, ver
    chat/salas.py) queda aparte: para quien no las tiene, "quien tiene reunion
    manana" se responde "no tengo acceso a agendas", y esa respuesta no puede
    servirse del caché a quien si las tiene (ni al reves) durante 10 minutos.
    """
    from . import salas

    if ctx.es_gerencia:
        base = "gerencia"
    elif ctx.rol == PerfilUsuario.SIN_ACCESO:
        return "sin_acceso"
    else:
        base = f"ejecutivo:{ctx.familia or f'emp{ctx.employee_id or 0}'}"
    correo = getattr(getattr(ctx, "usuario", None), "email", "")
    return f"{base}+agenda" if salas.usuario_habilitado(correo) else base
