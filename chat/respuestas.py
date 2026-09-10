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
VERSION = "v8"


def _canonica(mensaje):
    """Ignora acentos, mayusculas, puntuacion y espacios de mas.

    Asi "¿Quien esta fuera hoy?" y "quien esta fuera hoy" comparten respuesta.
    La puntuacion no se le quita a `normalizar` porque el router la usa.
    """
    texto = normalizar(mensaje)
    limpio = "".join(c if c.isalnum() else " " for c in texto)
    return " ".join(limpio.split())


def clave(mensaje, hoy, ambito=""):
    """`ambito` separa el caché por lo que cada rol puede ver: sin él, un
    ejecutivo recibiría la respuesta completa que se armó para un gerente
    (ver chat/perfil.py::ambito_cache)."""
    firma = f"{VERSION}|{ambito}|{hoy.isoformat()}|{_canonica(mensaje)}"
    return "resp:" + hashlib.sha1(firma.encode("utf-8")).hexdigest()


def obtener(mensaje, hoy, ambito=""):
    return cache.get(clave(mensaje, hoy, ambito))


def guardar(mensaje, hoy, respuesta, ambito=""):
    """Guarda solo respuestas definitivas.

    Ni "sin_datos" (el modelo dijo que no sabe: puede cambiar si se agrega el
    dato) ni "no_disponible" (Gemini esta caido o sin creditos: si no, se
    seguiria avisando que no responde durante toda la ventana, incluso
    despues de que el proveedor se recupere) se cachean.
    """
    if not respuesta:
        return
    meta = respuesta.get("meta", {})
    if meta.get("intencion") in ("sin_datos", "no_disponible", "bloqueada"):
        return
    cache.set(clave(mensaje, hoy, ambito), respuesta, settings.RESPUESTA_CACHE_TTL)
