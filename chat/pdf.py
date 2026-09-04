"""Extrae texto de PDFs para que entren al mismo indice que los .md y .txt.

Lo que se hace aca decide la calidad de todo lo demas: un embedding de un texto
mal extraido recupera basura con mucha seguridad.
"""

import logging
import re
import unicodedata
from collections import Counter

logger = logging.getLogger(__name__)

MIN_PAGINAS_PARA_LIMPIAR = 3
# Cuantas lineas del borde de cada pagina se revisan buscando repeticiones. Los
# .docx exportados traen una tabla de control de version de varias lineas, no
# un encabezado de una sola.
LINEAS_BORDE = 6
# una linea que aparece en mas de este porcentaje de paginas es encabezado o pie
UMBRAL_REPETICION = 0.6
# una linea mas corta que esto no venia envuelta: es un titulo o un cierre de
# parrafo, y unirla con la siguiente destruye la estructura del documento
LARGO_ENVUELTO = 60
# Los .docx exportados a PDF traen espacios de ancho cero que rompen las
# comparaciones de texto sin que se vean.
INVISIBLES = dict.fromkeys(map(ord, "\u200b\u200c\u200d\ufeff\xa0"), " ")
# Lineas del indice: "ANEXO III ......... 4". No son contenido.
INDICE = re.compile(r"\.{5,}\s*\d*\s*$")
# Numeral solo en su linea ("I." y en la siguiente "Objetivo"): van juntos.
NUMERAL_SUELTO = re.compile(r"^\s*(?:[IVXLC]{1,5}|\d{1,2})[.)]?\s*$")
FIN_DE_FRASE = (".", ":", ";", "!", "?", "•", "-", "—")


def disponible():
    try:
        import pymupdf  # noqa: F401
    except ImportError:
        return False
    return True


def _lineas_repetidas(paginas):
    """Encabezados y pies: se repiten en casi todas las paginas y ensucian todo."""
    if len(paginas) < MIN_PAGINAS_PARA_LIMPIAR:
        return set()

    conteo = Counter()
    for texto in paginas:
        lineas = [l.strip() for l in texto.splitlines() if l.strip()]
        # solo se miran los bordes de la pagina, no el cuerpo
        for linea in lineas[:LINEAS_BORDE] + lineas[-3:]:
            if len(linea) < 120:
                conteo[linea] += 1

    minimo = max(2, int(len(paginas) * UMBRAL_REPETICION))
    return {linea for linea, veces in conteo.items() if veces >= minimo}


def _numero_de_pagina(linea):
    limpia = linea.strip()
    return bool(re.fullmatch(r"[-–—\s]*\d{1,4}\s*(de|/)?\s*\d{0,4}[-–—\s]*", limpia))


def extraer(ruta):
    """Devuelve el texto del PDF, o "" si no se puede leer.

    Corrige las ligaduras tipograficas (ﬁ, ﬂ) que los PDF traen como un solo
    caracter: sin esto "planiﬁcacion" nunca coincide con "planificacion" y la
    seccion queda invisible para la busqueda.
    """
    try:
        import pymupdf
    except ImportError:
        logger.warning("pymupdf no esta instalado: se ignora %s", ruta)
        return ""

    try:
        with pymupdf.open(ruta) as doc:
            paginas = [(p.get_text("text") or "").translate(INVISIBLES) for p in doc]
    except Exception as error:
        logger.warning("no se pudo leer %s: %s", ruta, error)
        return ""

    if not any(p.strip() for p in paginas):
        logger.warning(
            "%s no tiene capa de texto (probablemente escaneado): necesita OCR", ruta
        )
        return ""

    basura = _lineas_repetidas(paginas)
    salida = []
    for texto in paginas:
        for linea in texto.splitlines():
            limpia = linea.rstrip()
            if not limpia.strip():
                salida.append("")
                continue
            if limpia.strip() in basura or _numero_de_pagina(limpia):
                continue
            if INDICE.search(limpia):   # linea del indice, no contenido
                continue
            salida.append(limpia)
        salida.append("")

    # NFKC descompone las ligaduras manteniendo acentos y enies
    texto = unicodedata.normalize("NFKC", "\n".join(salida))
    texto = "\n".join(_unir_envueltas(texto.splitlines()))
    texto = re.sub(r"\n{3,}", "\n\n", texto)
    return texto.strip()


def _unir_envueltas(lineas):
    """Reconstruye los parrafos que el PDF partio por ancho de columna.

    Solo se unen las lineas que de verdad venian envueltas: largas, sin cierre
    de frase, y seguidas de algo que empieza en minuscula. Unir sin esa
    condicion pega los titulos al texto ("I. Vacaciones Aspectos legales:") y
    el documento entero queda como una sola seccion.
    """
    salida = []
    for linea in lineas:
        actual = linea.strip()
        # "I." solo en su linea y "Objetivo" en la siguiente son un mismo titulo
        if salida and NUMERAL_SUELTO.fullmatch(salida[-1]) and actual:
            salida[-1] = f"{salida[-1].rstrip('.')}. {actual}"
            continue
        previa = salida[-1] if salida else ""
        if (previa and actual
                and len(previa) >= LARGO_ENVUELTO
                and not previa.endswith(FIN_DE_FRASE)
                and (actual[0].islower() or actual[0].isdigit())):
            salida[-1] = f"{previa} {actual}"
        else:
            salida.append(actual)
    return salida
