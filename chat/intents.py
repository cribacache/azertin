"""Normaliza texto y encuentra el area mencionada en una pregunta.

Ya no hay un router de reglas que decida la respuesta: eso lo hace Gemini con
las herramientas de `chat/herramientas.py`. Lo que queda aca es soporte para
esas herramientas, que reciben nombres y areas escritos en lenguaje natural y
necesitan resolverlos contra el directorio real.
"""

import unicodedata


def normalizar(texto):
    # NFKD y no NFD: los PDF exportados traen ligaduras (ﬁ, ﬂ) que NFD deja
    # intactas, y entonces "planiﬁcacion" nunca coincide con "planificacion".
    texto = unicodedata.normalize("NFKD", texto.lower())
    return "".join(c for c in texto if unicodedata.category(c) != "Mn")


def detectar_area(texto, nombres_area):
    """Nombre de area mencionado en la pregunta, si lo hay.

    Se compara contra las areas reales de BUK en vez de una lista escrita a
    mano: si manana crean un area nueva, funciona sin tocar el codigo.
    """
    mejor = None
    for nombre in nombres_area:
        clave = normalizar(nombre)
        if len(clave) >= 4 and clave in texto:
            if mejor is None or len(clave) > len(normalizar(mejor)):
                mejor = nombre
    return mejor
