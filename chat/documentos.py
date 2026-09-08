"""Responde desde archivos de texto que el equipo deja en `datos/`.

BUK contesta que pasa con las personas; estos documentos contestan las reglas
(politicas, procedimientos, quien autoriza que). Cada archivo .md o .txt se parte
por titulos y cada seccion se puntua contra las palabras de la pregunta.

El match es lexico, no semantico: encuentra la seccion cuando la pregunta usa
palabras del documento. Es la misma division en secciones que necesitaria un
modelo de lenguaje despues, asi que el trabajo no se pierde si se agrega uno.
"""

import logging
import re
from pathlib import Path

from django.conf import settings
from django.core.cache import cache

from . import embeddings, pdf
from .intents import normalizar

logger = logging.getLogger(__name__)

# La planilla de cuentas (.xlsx) NO va aca a proposito: trae RUTs y horas
# contractuales. Se lee estructurada en chat/cuentas.py, que solo toma la
# cuenta y el RUT como llave de cruce. Agregar ".xlsx" meteria esos datos al
# corpus de busqueda y podrian aparecer citados en una respuesta.
EXTENSIONES = (".md", ".txt", ".pdf")
IGNORADOS = ("leeme", "readme")  # documentacion del repo, no contenido consultable
MINIMO_SECCION = 120  # menos que esto es un encabezado, no una respuesta

# Una seccion larga mezcla varios temas y su embedding queda siendo el promedio
# de todos: no gana en ninguno. Partirla por parrafos es lo que mas mejora la
# recuperacion, mas que cambiar el algoritmo de busqueda.
MAX_SECCION = 700
# El puntaje lexico se expresa como fraccion de la pregunta que quedo cubierta,
# no como un valor absoluto. Los pesos IDF crecen con el corpus, asi que un
# umbral fijo que funciona con 20 fragmentos no significa nada con 981.
# Normalizar por el maximo alcanzable de cada pregunta lo hace comparable.
MINIMO_PUNTAJE = 0.45  # busqueda lexica sola, en corpus chico
TITULO_PESO = 1.5

# Con muchos fragmentos la busqueda lexica deja de discriminar: medido sobre los
# 981 reales, "cual es el anexo de recepcion" (0.91) puntua mas alto que "puedo
# aceptar un regalo de un cliente" (0.35), que si esta documentado. No hay
# umbral que acierte. Pasado este tamano, sin embeddings se prefiere no
# responder desde documentos antes que citar el reglamento equivocado.
MAX_FRAGMENTOS_SIN_EMBEDDINGS = 150
# Umbral cuando hay embeddings. Es alto a proposito: en espanol, cualquier
# pregunta de RRHH se parece a cualquier seccion de una politica de RRHH, y con
# un umbral bajo el buscador "encuentra" respuesta para todo. Medido sobre los
# 981 fragmentos reales: lo documentado puntua 0.70-0.82 y lo que no, 0.53-0.57
# (salvo un caso limite en 0.76). Al agregar documentos hay que volver a medirlo:
# el umbral depende del corpus, no es una constante universal.
MINIMO_MEZCLA = 0.67

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
# Un reglamento se divide solo: cada articulo es su propio fragmento, que es
# justo la granularidad que conviene para buscar.
_ARTICULO = re.compile(r"^\s*(Art[íi]culo\s+\d+\s*°?|Art\.\s*\d+\s*°?)\s*[:.\-]\s*(.*)$",
                       re.IGNORECASE)
# "LIBRO I: NORMAS DE ORDEN", "TITULO II: DE LA JORNADA"
_LIBRO = re.compile(r"^\s*(LIBRO|T[ÍI]TULO|CAP[ÍI]TULO|ANEXO)\s+[IVXLC\d]+\s*[:.\-]?\s*",
                    re.IGNORECASE)
_NUMERADO = re.compile(r"^\s*\d{1,2}(\.\d{1,2})*[.)]\s+\S")


# Un titulo numerado es corto ("III. Politica entre Privados"). Sin este limite,
# los items de una lista de definiciones ("5. Empresa: La entidad empleadora que
# contrata...") se toman como titulos y parten el articulo en pedazos sueltos.
LARGO_TITULO_NUMERADO = 60


def _es_titulo_plano(linea):
    limpia = linea.strip()
    if not limpia or len(limpia) > 90:
        return False
    if _LIBRO.match(limpia):
        return True
    if _ROMANO.match(limpia) or _NUMERADO.match(limpia):
        return len(limpia) <= LARGO_TITULO_NUMERADO
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
        articulo = _ARTICULO.match(linea)
        if articulo:
            # "Articulo 25°: La jornada..." trae titulo y cuerpo en la misma
            # linea: se separan para que cada articulo sea un fragmento propio.
            cerrar(es_portada=not hubo_titulo)
            hubo_titulo = True
            titulo = articulo.group(1).strip()
            cuerpo = [articulo.group(2).strip()] if articulo.group(2).strip() else []
        elif _es_titulo_plano(linea):
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


def _subdividir(seccion):
    """Parte una seccion larga en trozos por parrafo, conservando el titulo.

    El titulo se mantiene igual en todos los trozos para que la cita al usuario
    siga siendo la seccion real del documento.
    """
    cuerpo = seccion["cuerpo"]
    if len(cuerpo) <= MAX_SECCION:
        return [seccion]

    parrafos = [p.strip() for p in re.split(r"\n\s*\n", cuerpo) if p.strip()]
    if len(parrafos) < 2:
        parrafos = [l.strip() for l in cuerpo.splitlines() if l.strip()]

    trozos, actual = [], ""
    for parrafo in parrafos:
        if actual and len(actual) + len(parrafo) > MAX_SECCION:
            trozos.append(actual)
            actual = parrafo
        else:
            actual = f"{actual}\n\n{parrafo}" if actual else parrafo
    if actual:
        trozos.append(actual)

    return [{**seccion, "cuerpo": t} for t in trozos]


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
        if archivo.suffix.lower() == ".pdf":
            texto = pdf.extraer(archivo)
        else:
            try:
                texto = archivo.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
        if not texto.strip():
            continue
        secciones.extend(_partir(texto, archivo.name))

    secciones = [t for s in secciones for t in _subdividir(s)]
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
    # lo que sumaria una seccion que cubriera toda la pregunta en cuerpo y titulo
    maximo = (1 + TITULO_PESO) * sum(pesos.get(r, 0.0) for r in consulta)

    # Semantica: encuentra la seccion aunque la pregunta no comparta palabras
    # con ella. Si no hay clave o la API falla, queda solo la lexica.
    vectores = embeddings.vectores_de(secciones)
    consulta_vec = embeddings.vector_consulta(mensaje) if vectores else None

    if consulta_vec is None and len(secciones) > MAX_FRAGMENTOS_SIN_EMBEDDINGS:
        logger.warning(
            "%s fragmentos y sin embeddings: no se busca en documentos para no "
            "responder con una seccion equivocada. Corre 'manage.py indexar'.",
            len(secciones),
        )
        return []

    marcadas = []
    for seccion in secciones:
        cuerpo = {_raiz(p) for p in seccion["indice"]}
        titulo = {_raiz(p) for p in _palabras(seccion["titulo"])}
        puntaje = sum(pesos.get(r, 0.0) for r in consulta & cuerpo)
        puntaje += TITULO_PESO * sum(pesos.get(r, 0.0) for r in consulta & titulo)
        # Sin esto gana siempre la seccion mas larga, que acumula coincidencias
        # por volumen y no por ser la que responde.
        largo = (len(seccion["indice"]) or 1) / promedio
        puntaje /= 0.6 + 0.4 * largo

        # fraccion del peso informativo de la pregunta que esta seccion cubre
        lexico = min(puntaje / maximo, 1.0) if maximo else 0.0
        if consulta_vec:
            semantico = embeddings.similitud(
                consulta_vec, vectores.get(seccion.get("firma"))
            )
            mezcla = ((1 - settings.EMBEDDINGS_PESO) * lexico
                      + settings.EMBEDDINGS_PESO * semantico)
            if mezcla >= MINIMO_MEZCLA:
                marcadas.append((mezcla, seccion))
        elif puntaje >= MINIMO_PUNTAJE:
            marcadas.append((puntaje, seccion))

    if not marcadas:
        return []
    marcadas.sort(key=lambda par: -par[0])
    return [
        {"titulo": s["titulo"], "cuerpo": s["cuerpo"], "origen": s["origen"]}
        for puntaje, s in marcadas[:cuantas]
    ]
