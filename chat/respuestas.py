"""Cache de respuestas completas.

Guarda la respuesta ya armada para una pregunta. Repetirla dentro de la ventana
no consulta BUK ni gasta tokens, que es donde esta el ahorro real: la misma
pregunta ("quien esta fuera hoy") la hacen muchas personas el mismo dia.

La clave incluye la fecha porque casi todas las preguntas son relativas: la
respuesta a "quien esta de vacaciones hoy" no sirve manana.
"""

import hashlib

from django.conf import settings
from django.core.cache import cache

from .intents import normalizar

# Subir esto invalida todo lo cacheado. Cambiarlo al modificar como se arman las
# respuestas, para no servir el formato viejo desde el cache.
VERSION = "v6"


def _canonica(mensaje):
    """Ignora acentos, mayusculas, puntuacion y espacios de mas.

    Asi "¿Quien esta fuera hoy?" y "quien esta fuera hoy" comparten respuesta.
    La puntuacion no se le quita a `normalizar` porque el router la usa.
    """
    texto = normalizar(mensaje)
    limpio = "".join(c if c.isalnum() else " " for c in texto)
    return " ".join(limpio.split())


def clave(mensaje, hoy):
    firma = f"{VERSION}|{hoy.isoformat()}|{_canonica(mensaje)}"
    return "resp:" + hashlib.sha1(firma.encode("utf-8")).hexdigest()


def obtener(mensaje, hoy):
    return cache.get(clave(mensaje, hoy))


def guardar(mensaje, hoy, respuesta):
    """Guarda solo respuestas utiles: los errores no se cachean."""
    if not respuesta or respuesta.get("meta", {}).get("intencion") == "sin_datos":
        return
    cache.set(clave(mensaje, hoy), respuesta, settings.RESPUESTA_CACHE_TTL)
