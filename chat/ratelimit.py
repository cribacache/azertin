"""Límite de frecuencia simple, sobre el caché compartido (BD por defecto).

Ventana fija: un contador por (clave, ventana) que expira solo. No es exacto en
los bordes de la ventana, pero para frenar abuso o un script suelto alcanza y
no necesita Redis ni una dependencia nueva. Con varios workers de gunicorn el
contador es el mismo para todos porque el caché es la tabla de BD.

Falla abierto: si el caché tira error, deja pasar. Un límite caído no debe
tumbar el chat.
"""

import logging

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)


def _parse(spec):
    """'20/60' -> (20, 60): 20 solicitudes cada 60 segundos."""
    cantidad, ventana = spec.split("/")
    return int(cantidad), int(ventana)


def permitido(clave, spec):
    """Registra un golpe contra `clave` y dice si sigue dentro del límite.

    `spec` es 'N/segundos'. Devuelve False cuando se pasó del tope.
    """
    if not getattr(settings, "RATE_LIMIT_ACTIVO", False):
        return True
    try:
        cantidad, ventana = _parse(spec)
    except (ValueError, AttributeError):
        return True

    llave = f"rl:{clave}"
    try:
        if cache.add(llave, 1, ventana):
            return True
        try:
            actual = cache.incr(llave)
        except ValueError:
            # expiró entre el add y el incr: arranca la ventana de nuevo
            cache.set(llave, 1, ventana)
            return True
        return actual <= cantidad
    except Exception as error:  # caché caído: no bloquear por eso
        logger.warning("rate limit sin caché para %s: %s", clave, error)
        return True


def excedido(clave, spec):
    return not permitido(clave, spec)
