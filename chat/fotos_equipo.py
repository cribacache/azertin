"""Fotos del equipo desde la pagina publica de Azerta (azerta.cl/equipo).

BUK tambien tiene una foto por persona (`employee["picture_url"]`), pero las
de esta pagina son fotos profesionales curadas para el sitio publico: mejor
fuente para algo que se muestra hacia afuera (ej. la tarjeta de cumpleanos).
Se usa como primera fuente; si alguien no esta listado ahi (recien
ingresado, o la web no se actualizo todavia) el llamador puede caer a BUK.

La pagina no tiene API: se lee el HTML publico y se parsean los <img> con su
atributo alt (el nombre) y src (la foto). Sin login, sin cuota, sin clave.
"""

import logging
import re
import unicodedata
from html.parser import HTMLParser

import requests
from django.core.cache import cache

logger = logging.getLogger(__name__)

URL_EQUIPO = "https://azerta.cl/equipo/"
CACHE_KEY = "fotos_equipo:mapa"
# La pagina cambia poco (altas/bajas del equipo, no todos los dias): 6 horas
# alcanza de sobra y evita pegarle al sitio publico en cada consulta.
CACHE_TTL = 6 * 60 * 60
_EXTENSIONES = (".png", ".jpg", ".jpeg")


def _clave(texto):
    """Para comparar nombres sin importar tildes, mayusculas ni espacios de mas."""
    plano = unicodedata.normalize("NFKD", str(texto or "").lower())
    plano = "".join(c for c in plano if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", plano).strip()


class _ImgParser(HTMLParser):
    """Junta (alt, src) de cada <img>. El orden de los atributos en el HTML
    real no es siempre el mismo, por eso no conviene un regex sobre el HTML
    crudo: el parser de la libreria estandar no depende de ese orden."""

    def __init__(self):
        super().__init__()
        self.imagenes = []

    def handle_starttag(self, tag, attrs):
        if tag != "img":
            return
        atributos = dict(attrs)
        alt, src = atributos.get("alt"), atributos.get("src")
        if alt and src:
            self.imagenes.append((alt.strip(), src.strip()))


def _extraer(html):
    parser = _ImgParser()
    parser.feed(html)
    mapa = {}
    for alt, src in parser.imagenes:
        if not src.lower().endswith(_EXTENSIONES):
            continue  # logos del footer, iconos, etc: no son fotos de persona
        clave = _clave(alt)
        if clave:
            # La primera vez que aparece un nombre gana (la pagina repite a
            # veces la misma persona en el switch Chile/Peru o en un bloque
            # destacado arriba): no tiene sentido pisarla por una posterior.
            mapa.setdefault(clave, src)
    return mapa


def _mapa(forzar=False):
    if not forzar:
        cacheado = cache.get(CACHE_KEY)
        if cacheado is not None:
            return cacheado
    try:
        respuesta = requests.get(URL_EQUIPO, timeout=15)
        respuesta.raise_for_status()
    except requests.RequestException as error:
        logger.warning("no se pudo leer %s: %s", URL_EQUIPO, error)
        return {}
    mapa = _extraer(respuesta.text)
    cache.set(CACHE_KEY, mapa, CACHE_TTL)
    return mapa


def url_de(nombre, forzar=False):
    """URL de la foto de `nombre` en azerta.cl/equipo, o None si no aparece.

    Primero busca coincidencia exacta del nombre normalizado. Si no hay,
    prueba coincidencia parcial: BUK suele traer el nombre completo (con
    ambos apellidos) y la web a veces solo el primer apellido, o al reves.
    """
    mapa = _mapa(forzar=forzar)
    if not mapa:
        return None

    clave = _clave(nombre)
    if clave in mapa:
        return mapa[clave]

    tokens_nombre = set(clave.split())
    if not tokens_nombre:
        return None

    candidatas = [
        (url, clave_web) for clave_web, url in mapa.items()
        if set(clave_web.split()) and set(clave_web.split()) <= tokens_nombre
    ]
    if len(candidatas) == 1:
        return candidatas[0][0]
    return None
