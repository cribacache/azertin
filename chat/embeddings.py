"""Busqueda semantica sobre los documentos.

La busqueda por palabras tiene un techo claro: "cuantos dias me corresponden" y
"tienen derecho a quince dias habiles" significan lo mismo y no comparten una
sola palabra. Los embeddings comparan significado, no letras.

Todo esto es opcional: sin clave o si la API falla, la busqueda lexica sigue
funcionando igual. Nunca debe tumbar una respuesta.
"""

import hashlib
import json
import logging
import math
import re
import threading
import time

from django.conf import settings

logger = logging.getLogger(__name__)

_lock = threading.Lock()
# task_type distinto para documento y consulta: es lo que le dice al modelo que
# un texto se va a indexar y el otro se va a buscar. Mejora notablemente el match.
TAREA_DOCUMENTO = "RETRIEVAL_DOCUMENT"
TAREA_CONSULTA = "RETRIEVAL_QUERY"


def disponible():
    return bool(settings.GEMINI_API_KEY) and settings.EMBEDDINGS_ACTIVOS


def firma(texto):
    """Identifica un fragmento por contenido: si no cambia, no se recalcula."""
    material = f"{settings.EMBEDDINGS_MODELO}|{settings.EMBEDDINGS_DIMENSIONES}|{texto}"
    return hashlib.sha1(material.encode("utf-8")).hexdigest()


def _cargar_disco():
    ruta = settings.EMBEDDINGS_ARCHIVO
    if not ruta.exists():
        return {}
    try:
        return json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("no se pudo leer %s, se recalculan", ruta)
        return {}


def _guardar_disco(cache_):
    """Guarda fusionando con lo que haya en disco.

    Dos indexados en paralelo escriben el mismo archivo y el ultimo en guardar
    borraria los vectores del otro. Releer y fusionar antes de escribir hace que
    el trabajo se sume en vez de pisarse.
    """
    try:
        combinado = _cargar_disco()
        combinado.update(cache_)
        temporal = settings.EMBEDDINGS_ARCHIVO.with_suffix(".tmp")
        temporal.write_text(json.dumps(combinado), encoding="utf-8")
        temporal.replace(settings.EMBEDDINGS_ARCHIVO)
    except OSError as error:
        logger.warning("no se pudo guardar el indice: %s", error)


def _espera_pedida(error):
    """Segundos que la propia API pide esperar tras un 429.

    Adivinar la pausa no sirve: el mensaje trae el valor exacto y va de 0 a 60
    segundos segun cuanto falte para que se renueve la ventana del minuto.
    """
    texto = str(error)
    if "RESOURCE_EXHAUSTED" not in texto and "429" not in texto:
        return None
    encontrado = re.search(r"retry in ([\d.]+)s", texto)
    if encontrado:
        return min(float(encontrado.group(1)) + 2, 75)
    return 30.0


def _pedir(textos, tarea, reintentos=None):
    """Llama a la API. Devuelve una lista de vectores, o None si falla."""
    from google.genai import types

    from . import asistente

    reintentos = settings.EMBEDDINGS_REINTENTOS if reintentos is None else reintentos
    for intento in range(reintentos + 1):
        try:
            cliente = asistente._cliente_gemini()
            respuesta = cliente.models.embed_content(
                model=settings.EMBEDDINGS_MODELO,
                contents=textos,
                config=types.EmbedContentConfig(
                    task_type=tarea,
                    output_dimensionality=settings.EMBEDDINGS_DIMENSIONES,
                ),
            )
            return [list(e.values) for e in respuesta.embeddings]
        except Exception as error:
            espera = _espera_pedida(error)
            if espera is not None and intento < reintentos:
                logger.info("cuota agotada, esperando %.0fs", espera)
                time.sleep(espera)
                continue
            if espera is None:
                logger.warning("embeddings fallaron: %s", str(error)[:160])
            return None
    return None


def indexar(secciones, forzar=False, progreso=None):
    """Calcula y guarda el vector de cada seccion. Devuelve (indexados, fallidos).

    Solo se piden los que no estan en el indice, asi que agregar un documento
    nuevo cuesta unicamente sus propios fragmentos.
    """
    if not disponible() or not secciones:
        return 0, 0

    with _lock:
        cache_ = {} if forzar else _cargar_disco()
        pendientes, claves = [], []
        for seccion in secciones:
            texto = f"{seccion['titulo']}\n{seccion['cuerpo']}"
            clave = firma(texto)
            seccion["firma"] = clave
            if clave not in cache_:
                pendientes.append(texto[: settings.EMBEDDINGS_MAX_CARACTERES])
                claves.append(clave)

        # La capa gratuita cuenta ELEMENTOS por minuto, no llamadas: un lote de
        # 20 gasta 20. Se marca el ritmo por adelantado en vez de chocar contra
        # el limite y esperar el rechazo.
        pausa = 60.0 * settings.EMBEDDINGS_LOTE / max(settings.EMBEDDINGS_RPM, 1)

        pedidos, fallidos = 0, 0
        for i in range(0, len(pendientes), settings.EMBEDDINGS_LOTE):
            lote = pendientes[i:i + settings.EMBEDDINGS_LOTE]
            if i:
                time.sleep(pausa)
            vectores = _pedir(lote, TAREA_DOCUMENTO)
            if vectores is None:
                fallidos += len(lote)
                continue
            for clave, vector in zip(claves[i:i + settings.EMBEDDINGS_LOTE], vectores):
                cache_[clave] = vector
            pedidos += len(lote)
            # Se guarda en cada lote, no al final: un indexado de 900 fragmentos
            # tarda unos 11 minutos por la cuota, y si se corta a la mitad no
            # tiene sentido perder lo ya calculado. Al reanudar solo se piden
            # los que faltan.
            _guardar_disco(cache_)
            if progreso:
                progreso(pedidos, len(pendientes))
        if fallidos:
            logger.warning("%s fragmentos quedaron sin indexar", fallidos)
        return pedidos, fallidos


def vectores_de(secciones):
    """Mapa {firma: vector} para las secciones dadas, desde el indice en disco."""
    if not disponible():
        return {}
    cache_ = _cargar_disco()
    salida = {}
    for seccion in secciones:
        texto = f"{seccion['titulo']}\n{seccion['cuerpo']}"
        clave = seccion.get("firma") or firma(texto)
        seccion["firma"] = clave
        if clave in cache_:
            salida[clave] = cache_[clave]
    return salida


def vector_consulta(texto):
    """Vector de la pregunta del usuario, o None.

    Sin reintentos a proposito: si la cuota esta agotada hay que caer a la
    busqueda lexica de inmediato. Esperar los 30-60 segundos que pide la API
    tiene sentido al indexar en lote, pero no con alguien esperando la
    respuesta en el chat.
    """
    if not disponible() or not texto.strip():
        return None
    vectores = _pedir([texto[: settings.EMBEDDINGS_MAX_CARACTERES]],
                      TAREA_CONSULTA, reintentos=0)
    return vectores[0] if vectores else None


def similitud(a, b):
    """Coseno. A esta escala (cientos de fragmentos) no hace falta numpy."""
    if not a or not b or len(a) != len(b):
        return 0.0
    punto = na = nb = 0.0
    for x, y in zip(a, b):
        punto += x * y
        na += x * x
        nb += y * y
    if na <= 0 or nb <= 0:
        return 0.0
    return punto / math.sqrt(na * nb)
