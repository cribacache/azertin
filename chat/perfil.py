"""Quién es la persona que está preguntando.

Cruza la cuenta de Google con la que inició sesión (`user.email`) contra el
empleado de BUK que tiene ese correo, y arma un `Contexto` con lo que necesita
`chat/autorizacion.py` para decidir qué puede ver: el rol y la familia de rol
(la "jerarquía") de quien pregunta.

Si no hay match en BUK (contratista, cuenta de servicio, correo que no calza),
el contexto queda sin `employee_id` ni `familia`: `autorizacion.py` lo trata
como un ejecutivo que solo se puede consultar a sí mismo.
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
    familia: str = ""
    area: str = ""
    cuentas: list = field(default_factory=list)
    usuario: object = None  # el User, solo para registrar eventos de seguridad

    @property
    def es_gerencia(self):
        return self.rol == PerfilUsuario.GERENCIA


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
    """
    if ctx.es_gerencia:
        return "gerencia"
    if ctx.rol == PerfilUsuario.SIN_ACCESO:
        return "sin_acceso"
    return f"ejecutivo:{ctx.familia or f'emp{ctx.employee_id or 0}'}"
