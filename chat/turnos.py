"""Turno, modalidad y puesto de cada persona.

BUK no tiene este dato (ver chat/buk.py): vive en una planilla aparte que el
equipo de Personas mantiene en Drive ("Turnos Tanica y Digital") y que
chat/drive.py sincroniza como .csv -solo esa hoja, sumada a mano en
settings.DRIVE_HOJAS_PERMITIDAS, nunca "cualquier Sheet de la carpeta".

Se lee estructurada aca, en vez de dejarla solo en el corpus de busqueda por
texto (chat/documentos.py): es una tabla de bastante mas de 60 filas, y
buscar por fragmentos de texto puede devolver la fila de otra persona en vez
de la que se pregunto. Mismo motivo que chat/cuentas.py con la planilla de
cuentas.

Forma real de la planilla: varias areas apiladas en una sola hoja, cada una
con su propia fila de titulo (una sola celda no vacia, ej. "Digital") seguida
de su propio encabezado de columnas ("Nombre,Cargo,Forma de trabajo,
Modalidad,N° puesto,Observacion"). Se detectan por forma, no por posicion,
porque cuantas areas haya puede cambiar.
"""

import csv
import io
import re
import unicodedata
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
