"""Turno, modalidad y puesto de cada persona; y que semanas le toca
presencial si su turno es rotativo.

BUK no tiene este dato (ver chat/buk.py): vive en una planilla aparte que el
equipo de Personas mantiene en Drive ("Turnos Tanica y Digital") y que
chat/drive.py sincroniza como .csv -solo esa hoja, sumada a mano en
settings.DRIVE_HOJAS_PERMITIDAS, nunca "cualquier Sheet de la carpeta".

Se lee estructurada aca, en vez de dejarla solo en el corpus de busqueda por
texto (chat/documentos.py): es una tabla de bastante mas de 60 filas, y
buscar por fragmentos de texto puede devolver la fila de otra persona en vez
de la que se pregunto. Mismo motivo que chat/cuentas.py con la planilla de
cuentas.

Forma real de la planilla (Hoja 1): varias areas apiladas en una sola hoja,
cada una con su propia fila de titulo (una sola celda no vacia, ej.
"Digital") seguida de su propio encabezado de columnas ("Nombre,Cargo,Forma
de trabajo,Modalidad,N° puesto,Observacion"). Se detectan por forma, no por
posicion, porque cuantas areas haya puede cambiar.

"Presencial" (modalidad "Presencial", forma de trabajo "Permanente") no es lo
mismo que "hibrido con turno rotativo" (modalidad "Hibrido", forma de trabajo
"Turno 1" o "Turno 2"): a esas ultimas personas les toca presencial solo
semana por medio, alternando entre los dos turnos. Que semana le toca a cada
turno esta en una segunda pestaña de la MISMA planilla ("Hoja 2"), una fila
por turno con los lunes de sus semanas presenciales -toda esa semana (lunes a
viernes) es presencial, salvo feriado. Esa pestaña no la trae el exportador a
CSV de chat/drive.py (solo exporta la primera visible), asi que se lee en
vivo con drive.valores_de_hoja(), no desde el .csv sincronizado a disco.
"""

import csv
import io
import re
import unicodedata
from datetime import date, timedelta
from pathlib import Path

from django.conf import settings
from django.core.cache import cache


def _clave(texto):
    """Para comparar nombres sin importar tildes, mayusculas ni espacios de mas."""
    plano = unicodedata.normalize("NFKD", str(texto or "").lower())
    plano = "".join(c for c in plano if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", plano).strip()


def _tokens(nombre):
    return {t for t in _clave(nombre).split() if len(t) >= 2}


def _carpeta():
    """Misma carpeta (y mismo disparo de sincronizacion) que chat/documentos.py."""
    if getattr(settings, "DOCUMENTOS_FUENTE", "local") == "drive":
        from . import drive

        drive.sincronizar_si_toca(settings.DRIVE_CACHE_DIR)
        return Path(settings.DRIVE_CACHE_DIR)
    return Path(settings.DOCUMENTOS_DIR)


NOMBRE_EN_DRIVE = "Turnos Tanica y Digital"


def archivo():
    carpeta = _carpeta()
    if not carpeta.exists():
        return None
    if getattr(settings, "DOCUMENTOS_FUENTE", "local") == "drive":
        # Con mas de una Sheet permitida sincronizada (ver chat/cuentas.py),
        # "el primer .csv" ya no alcanza para identificar esta planilla.
        hallazgos = sorted(carpeta.glob(f"{NOMBRE_EN_DRIVE}*.csv"))
    else:
        hallazgos = sorted(carpeta.glob("*.csv"))
    return hallazgos[0] if hallazgos else None


def _valor(celdas, i):
    return celdas[i].strip() if i < len(celdas) and celdas[i] else ""


def _filas(texto_csv):
    # StringIO, no splitlines(): una "Observacion" con salto de linea adentro
    # de comillas es una sola celda para el CSV, no dos filas.
    area_actual, filas = "", []
    for cruda in csv.reader(io.StringIO(texto_csv)):
        celdas = [c.strip() for c in cruda]
        no_vacias = [c for c in celdas if c]
        if not no_vacias:
            continue  # separador entre areas
        if celdas[0].lower() == "nombre":
            continue  # encabezado de columnas, se repite por cada area
        if len(no_vacias) == 1:
            area_actual = no_vacias[0]  # fila de titulo de area
            continue
        nombre = celdas[0].strip()
        if not nombre:
            continue
        filas.append({
            "nombre": nombre,
            "area": area_actual,
            "cargo": _valor(celdas, 1),
            "forma_trabajo": _valor(celdas, 2),
            "modalidad": _valor(celdas, 3),
            "puesto": _valor(celdas, 4),
            "observacion": _valor(celdas, 5),
        })
    return filas


def cargar(forzar=False):
    """Lista de filas {nombre, area, cargo, forma_trabajo, modalidad, puesto,
    observacion}. Cacheada por fecha de modificacion del archivo."""
    ruta = archivo()
    if ruta is None:
        return []

    firma = (str(ruta), ruta.stat().st_mtime_ns)
    if not forzar:
        cacheado = cache.get("turnos:filas")
        if cacheado is not None and cacheado.get("firma") == firma:
            return cacheado["filas"]

    try:
        texto = ruta.read_text(encoding="utf-8")
    except OSError:
        return []

    filas = _filas(texto)
    cache.set("turnos:filas", {"firma": firma, "filas": filas}, settings.BUK_CACHE_TTL)
    return filas


def buscar(nombre):
    """La fila que mejor matchea `nombre` (por tokens en comun), o None.

    El nombre de esta planilla no siempre coincide letra por letra con el de
    BUK (espacios de mas, con o sin segundo apellido), asi que compara por
    cuantas palabras del nombre tienen en comun en vez de exigir igualdad.
    """
    objetivo = _tokens(nombre)
    if not objetivo:
        return None
    mejor, mejor_puntaje = None, 0
    for fila in cargar():
        puntaje = len(_tokens(fila["nombre"]) & objetivo)
        if puntaje > mejor_puntaje:
            mejor, mejor_puntaje = fila, puntaje
    return mejor


def _coincide(valor, filtro):
    """Comparacion sin tildes/mayusculas; el filtro matchea si es substring
    del valor ("hibrido" matchea "Hibrido", "turno" matchea "Turno 1")."""
    if not filtro:
        return True
    return _clave(filtro) in _clave(valor)


def listar(modalidad=None, forma_trabajo=None, area=None):
    """Filas que matchean TODOS los filtros dados (los que se omiten no filtran)."""
    return [
        fila for fila in cargar()
        if _coincide(fila["modalidad"], modalidad)
        and _coincide(fila["forma_trabajo"], forma_trabajo)
        and _coincide(fila["area"], area)
    ]


# ---------------------------------------------------------------------------
# Semanas presenciales del turno rotativo (Hoja 2 de la misma planilla)
# ---------------------------------------------------------------------------

NOMBRE_HOJA_SEMANAS = "Hoja 2"
CACHE_SEMANAS = "turnos:semanas_presenciales:v1"

_MESES = {
    "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
    "jul": 7, "ago": 8, "sep": 9, "oct": 10, "nov": 11, "dic": 12,
}
_MESES_NOMBRE = {
    1: "enero", 2: "febrero", 3: "marzo", 4: "abril", 5: "mayo", 6: "junio",
    7: "julio", 8: "agosto", 9: "septiembre", 10: "octubre", 11: "noviembre",
    12: "diciembre",
}


def _fecha_hoja2(texto):
    """"21-sept-2026" -> date(2026, 9, 21). None si no calza el formato."""
    m = re.match(r"^\s*(\d{1,2})-([a-zA-Z]{3,4})-(\d{4})\s*$", texto or "")
    if not m:
        return None
    dia, mes_texto, anio = m.groups()
    mes = _MESES.get(mes_texto.lower()[:3])
    if not mes:
        return None
    try:
        return date(int(anio), mes, int(dia))
    except ValueError:
        return None


def _semanas_presenciales(forzar=False):
    """{"turno 1": {lunes, lunes, ...}, "turno 2": {...}}: los lunes de cada
    semana en que ese turno tiene presencial (Hoja 2). Diccionario vacio si
    Drive no esta configurado o la pestaña no se pudo leer -nunca lanza,
    quien llama trata "sin datos" como "no se puede determinar".
    """
    if not forzar:
        cacheado = cache.get(CACHE_SEMANAS)
        if cacheado is not None:
            return cacheado

    if getattr(settings, "DOCUMENTOS_FUENTE", "local") != "drive":
        return {}

    from . import drive

    try:
        spreadsheet_id = drive.id_de_archivo(NOMBRE_EN_DRIVE)
        if not spreadsheet_id:
            return {}
        filas = drive.valores_de_hoja(spreadsheet_id, NOMBRE_HOJA_SEMANAS)
    except Exception:
        return {}

    semanas = {}
    for fila in filas:
        if not fila:
            continue
        turno = _clave(fila[0])
        if not turno:
            continue
        semanas[turno] = {f for f in (_fecha_hoja2(c) for c in fila[1:]) if f}

    cache.set(CACHE_SEMANAS, semanas, settings.BUK_CACHE_TTL)
    return semanas


def _semana_relativa(fecha, hoy):
    """Como nombrar la semana de `fecha` sin dar la fecha exacta, salvo que
    este lejos de `hoy` (asistente.INSTRUCCIONES le pide al modelo usar esto
    tal cual, no calcular ni mencionar la fecha por su cuenta)."""
    lunes_consulta = fecha - timedelta(days=fecha.weekday())
    lunes_hoy = hoy - timedelta(days=hoy.weekday())
    diferencia = (lunes_consulta - lunes_hoy).days // 7
    if diferencia == 0:
        return "esta semana"
    if diferencia == 1:
        return "la próxima semana"
    if diferencia == -1:
        return "la semana pasada"
    return f"la semana del {lunes_consulta.day} de {_MESES_NOMBRE[lunes_consulta.month]}"


def info_presencial(fila, fecha, hoy=None):
    """Si `fila` (una fila de cargar()) tiene presencial en la semana de
    `fecha`, y como nombrar esa semana. None si no se puede determinar
    (modalidad no reconocida, o es hibrida pero no hay datos de Hoja 2 para
    su turno).

    "Presencial" (forma de trabajo "Permanente"): siempre, cualquier semana.
    "Hibrido" (turno rotativo): alterna semana por medio segun Hoja 2; la
    modalidad sola ("Hibrido") no alcanza para saber ESTA semana puntual.
    """
    hoy = hoy or date.today()
    modalidad = _clave(fila.get("modalidad"))
    semana = _semana_relativa(fecha, hoy)

    if "presencial" in modalidad:
        return {"es_presencial": True, "semana": semana, "siempre": True}
    if "hibrido" not in modalidad:
        return None

    semanas_turno = _semanas_presenciales().get(_clave(fila.get("forma_trabajo")))
    if semanas_turno is None:
        return None
    lunes = fecha - timedelta(days=fecha.weekday())
    return {"es_presencial": lunes in semanas_turno, "semana": semana, "siempre": False}
