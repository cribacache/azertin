"""Responde desde archivos de texto que el equipo deja en `datos/`.

BUK contesta que pasa con las personas; estos documentos contestan las reglas
(politicas, procedimientos, quien autoriza que). Cada archivo .md o .txt se parte
por titulos y cada seccion se puntua contra las palabras de la pregunta.

El match es lexico, no semantico: encuentra la seccion cuando la pregunta usa
palabras del documento. Es la misma division en secciones que necesitaria un
modelo de lenguaje despues, asi que el trabajo no se pierde si se agrega uno.
"""

import re
from pathlib import Path

from django.conf import settings
from django.core.cache import cache

from .intents import normalizar

EXTENSIONES = (".md", ".txt")
IGNORADOS = ("leeme", "readme")  # documentacion del repo, no contenido consultable
MINIMO_SECCION = 120  # menos que esto es un encabezado, no una respuesta
MINIMO_PUNTAJE = 1.6  # por debajo de esto, la coincidencia es casualidad

VACIAS = {
    "que", "cual", "cuales", "como", "cuando", "donde", "quien", "quienes",
    "para", "por", "con", "sin", "los", "las", "del", "una", "uno", "unos",
    "unas", "mi", "mis", "tu", "sus", "sobre", "hay", "son", "esta", "estan",
    "puedo", "puede", "tengo", "tiene", "debo", "debe", "si", "no", "el", "la",
    "de", "en", "y", "o", "a", "es", "se", "al", "lo", "un", "me", "te",
}


def _palabras(texto):
    limpio = "".join(c if c.isalnum() else " " for c in normalizar(texto))
    return {p for p in limpio.split() if len(p) > 2 and p not in VACIAS}


# Titulos de un texto plano (tipico de un PDF exportado): "Aspectos legales:",
# "I. Vacaciones", "2. Solicitud". Sin esto, un archivo sin markdown queda como
# una sola seccion gigante y cualquier coincidencia devuelve el documento entero.
_ROMANO = re.compile(r"^\s*[IVXLC]{1,5}[.)]\s+\S")
_NUMERADO = re.compile(r"^\s*\d{1,2}(\.\d{1,2})*[.)]\s+\S")


def _es_titulo_plano(linea):
    limpia = linea.strip()
    if not limpia or len(limpia) > 90:
        return False
    if _ROMANO.match(limpia) or _NUMERADO.match(limpia):
        return True
    # "Aspectos legales:" es titulo; una frase larga terminada en ":" no lo es.
    return limpia.endswith(":") and len(limpia.split()) <= 8


def _partir_plano(texto, origen):
    secciones, titulo, cuerpo = [], Path(origen).stem, []
    hubo_titulo = False

    def cerrar(es_portada):
        contenido = "\n".join(cuerpo).strip()
        if not contenido:
            return
        # Solo el bloque anterior al primer titulo es portada ("Politica de
        # Vacaciones / Azerta"): compite por las mismas palabras y no responde
        # nada. Una seccion con titulo se conserva aunque sea corta.
        if es_portada and len(contenido) < MINIMO_SECCION:
            return
        secciones.append({"titulo": titulo, "cuerpo": contenido, "origen": origen})

    for linea in texto.splitlines():
        if _es_titulo_plano(linea):
            cerrar(es_portada=not hubo_titulo)
            hubo_titulo = True
            titulo, cuerpo = linea.strip().rstrip(":"), []
        else:
            cuerpo.append(linea)
    cerrar(es_portada=not hubo_titulo)
    return secciones


def _partir(texto, origen):
    """Divide por titulos markdown; si no hay, por titulos de texto plano."""
    partes = re.split(r"^#{1,6}\s+(.+)$", texto, flags=re.MULTILINE)
    secciones = []

    if len(partes) == 1:
        return _partir_plano(texto, origen)

    preambulo = partes[0].strip()
    if preambulo:
        secciones.append({"titulo": Path(origen).stem, "cuerpo": preambulo, "origen": origen})

    for i in range(1, len(partes), 2):
        titulo = partes[i].strip()
        cuerpo = partes[i + 1].strip() if i + 1 < len(partes) else ""
        if cuerpo:
            secciones.append({"titulo": titulo, "cuerpo": cuerpo, "origen": origen})
    return secciones


def cargar(forzar=False):
    """Lee la carpeta de documentos. Cacheada; se invalida al cambiar un archivo."""
    carpeta = Path(settings.DOCUMENTOS_DIR)
    if not carpeta.exists():
        return []

    archivos = sorted(
        p for p in carpeta.rglob("*")
        if p.is_file() and p.suffix.lower() in EXTENSIONES
        and not p.stem.lower().startswith(IGNORADOS)
    )
    firma = tuple((str(p), p.stat().st_mtime_ns) for p in archivos)

    if not forzar:
        cacheado = cache.get("docs:secciones")
        if cacheado is not None and cacheado.get("firma") == firma:
            return cacheado["secciones"]

    secciones = []
    for archivo in archivos:
        try:
            texto = archivo.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        secciones.extend(_partir(texto, archivo.name))

    for seccion in secciones:
        seccion["indice"] = _palabras(seccion["titulo"] + " " + seccion["cuerpo"])

    cache.set("docs:secciones", {"firma": firma, "secciones": secciones}, 300)
    return secciones


PREFIJO = 6  # "enfermo" y "enferme" comparten raiz; se tratan como la misma


def _raiz(palabra):
    return palabra[:PREFIJO] if len(palabra) > PREFIJO else palabra


def _pesos(secciones):
    """Peso de cada raiz segun en cuantas secciones aparece (idea de IDF).

    Sin esto, "vacaciones" —que esta en casi todas las secciones de una politica
    de vacaciones— vale lo mismo que "enfermo", que esta en una sola. La palabra
    rara es la que dice cual seccion responde.
    """
    import math

    total = max(len(secciones), 1)
    frecuencia = {}
    for seccion in secciones:
        for raiz in {_raiz(p) for p in seccion["indice"]}:
            frecuencia[raiz] = frecuencia.get(raiz, 0) + 1
    return {r: math.log(1 + total / n) for r, n in frecuencia.items()}


def buscar(mensaje, cuantas=3):
    """Las mejores secciones para la pregunta, de mayor a menor puntaje.

    El router de reglas usa solo la primera; al modelo se le pasan varias,
    porque una pregunta suele cruzar dos secciones ("cuantos dias me tocan y
    como los pido") y componer es justamente lo que el modelo hace bien.
    """
    consulta = {_raiz(p) for p in _palabras(mensaje)}
    if not consulta:
        return []

    secciones = cargar()
    if not secciones:
        return []
    pesos = _pesos(secciones)

    largos = [len(s["indice"]) or 1 for s in secciones]
    promedio = sum(largos) / len(largos)

    marcadas = []
    for seccion in secciones:
        cuerpo = {_raiz(p) for p in seccion["indice"]}
        titulo = {_raiz(p) for p in _palabras(seccion["titulo"])}
        puntaje = sum(pesos.get(r, 0.0) for r in consulta & cuerpo)
        puntaje += 1.5 * sum(pesos.get(r, 0.0) for r in consulta & titulo)
        # Sin esto gana siempre la seccion mas larga, que acumula coincidencias
        # por volumen y no por ser la que responde.
        largo = (len(seccion["indice"]) or 1) / promedio
        puntaje /= 0.6 + 0.4 * largo
        if puntaje >= MINIMO_PUNTAJE:
            marcadas.append((puntaje, seccion))

    if not marcadas:
        return []
    marcadas.sort(key=lambda par: -par[0])
    return [
        {"titulo": s["titulo"], "cuerpo": s["cuerpo"], "origen": s["origen"]}
        for puntaje, s in marcadas[:cuantas]
    ]


def responder(mensaje):
    """Mejor seccion para la pregunta, o None si ninguna alcanza el minimo."""
    encontradas = buscar(mensaje, cuantas=1)
    return encontradas[0] if encontradas else None
