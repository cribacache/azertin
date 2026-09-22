"""Azerta Finder: numero de contacto de cada persona, desde una planilla de
Drive (un .xlsx subido tal cual, fuera de BUK y de la carpeta de politicas).

Acceso restringido por lista de correos (settings.AZERTA_FINDER_USUARIOS),
mismo criterio que chat/salas.py::usuario_habilitado: es informacion de
contacto de terceros, confidencial, y se abre persona por persona, sin
excepcion por rol -ni gerencia ni un superusuario entran si no estan en la
lista.

Misma mecanica que chat/cuentas.py: el nombre del archivo tiene que estar en
settings.DRIVE_HOJAS_PERMITIDAS para que chat/drive.py lo sincronice (baja el
.xlsx completo, no pasa por el exportador de Sheets, que solo entrega la
primera pestaña de una Sheet nativa -esto no es una). Aca se lee de esa copia
local, cacheada por fecha de modificacion del archivo.
"""

import logging
from pathlib import Path

from django.conf import settings
from django.core.cache import cache

from .intents import normalizar

logger = logging.getLogger(__name__)

# Nombre tal cual lo deja chat/drive.py al sincronizar el .xlsx desde Drive
# (ver drive._nombre_local). El glob con "*" tolera el sufijo "-<id>" que se
# agrega solo si hubiera un choque de nombres.
NOMBRE_EN_DRIVE = "BBDD FIESTA 18 AÑOS V.24.10"

# Encabezados posibles para cada columna que Iris necesita, buscados como
# substring del encabezado ya normalizado (sin tildes, en minuscula): la
# planilla no la administra este proyecto, asi que no se asume el nombre
# exacto de cada columna.
_CLAVES_NOMBRE = ("nombre",)
_CLAVES_TELEFONO = ("telefono", "fono", "celular", "movil", "whatsapp", "contacto")


class FinderError(Exception):
    """Error al leer la planilla, con mensaje listo para el usuario."""


def usuario_habilitado(correo):
    """Si `correo` puede usar Azerta Finder: lista explicita de correos, sin
    excepcion por rol (ver el criterio en el docstring del modulo)."""
    permitidos = getattr(settings, "AZERTA_FINDER_USUARIOS", set())
    return (correo or "").strip().lower() in permitidos


def _usa_drive():
    return getattr(settings, "DOCUMENTOS_FUENTE", "local") == "drive"


def _carpeta():
    """Misma logica que chat/documentos.py, chat/turnos.py y chat/cuentas.py."""
    if _usa_drive():
        from . import drive

        drive.sincronizar_si_toca(settings.DRIVE_CACHE_DIR)
        return Path(settings.DRIVE_CACHE_DIR)
    return Path(settings.DOCUMENTOS_DIR)


def archivo():
    carpeta = _carpeta()
    if not carpeta.exists():
        return None
    patron = f"{NOMBRE_EN_DRIVE}*.xlsx" if _usa_drive() else "*.xlsx"
    hallazgos = sorted(carpeta.glob(patron))
    return hallazgos[0] if hallazgos else None


def _columna(encabezados, claves):
    for i, encabezado in enumerate(encabezados):
        plano = normalizar(str(encabezado or ""))
        if any(clave in plano for clave in claves):
            return i
    return None


def _valor(fila, idx):
    if idx is None or idx >= len(fila) or fila[idx] is None:
        return ""
    return str(fila[idx]).strip()


def cargar(forzar=False):
    """Filas de la planilla: [{"nombre", "telefono"}, ...].

    Cacheadas por fecha de modificacion del archivo: reemplazarlo en Drive
    actualiza los contactos sin reiniciar nada. Lanza FinderError si el
    archivo no esta (todavia no se sincronizo, o no esta en
    DRIVE_HOJAS_PERMITIDAS) o si no se pudo leer -asi la herramienta que
    llama puede avisar en vez de decir "no encontrada" por un problema que
    no tiene nada que ver con el nombre buscado.
    """
    ruta = archivo()
    if ruta is None:
        raise FinderError("Azerta Finder no está disponible todavía. Avisa al equipo técnico.")

    firma = (str(ruta), ruta.stat().st_mtime_ns)
    if not forzar:
        cacheado = cache.get("finder:filas")
        if cacheado is not None and cacheado.get("firma") == firma:
            return cacheado["filas"]

    try:
        import openpyxl
    except ImportError:
        raise FinderError("Azerta Finder no está disponible todavía. Avisa al equipo técnico.")

    try:
        libro = openpyxl.load_workbook(ruta, data_only=True, read_only=True)
        hoja = libro[libro.sheetnames[0]]
        crudas = list(hoja.iter_rows(values_only=True))
        libro.close()
    except Exception as error:
        logger.warning("azerta finder: no se pudo leer %s: %s", ruta.name, error)
        raise FinderError("No pude leer la planilla de contactos en este momento.")

    if not crudas:
        filas = []
    else:
        idx_nombre = _columna(crudas[0], _CLAVES_NOMBRE)
        idx_telefono = _columna(crudas[0], _CLAVES_TELEFONO)
        if idx_nombre is None or idx_telefono is None:
            raise FinderError(
                "No pude identificar las columnas de nombre y teléfono en la planilla.")
        filas = []
        for cruda in crudas[1:]:
            nombre = _valor(cruda, idx_nombre)
            if not nombre:
                continue
            filas.append({"nombre": nombre, "telefono": _valor(cruda, idx_telefono)})

    cache.set("finder:filas", {"firma": firma, "filas": filas}, settings.AZERTA_FINDER_CACHE_TTL)
    return filas


def buscar(nombre):
    """La fila que mejor matchea `nombre` (por tokens en comun): (fila, candidatos).

    `fila` es None si no matchea a nadie (candidatos tambien None) o si el
    nombre coincide con varias personas (candidatos trae los nombres)."""
    from . import personas

    filas = cargar()
    directorio = {i: {"nombre": f["nombre"]} for i, f in enumerate(filas)}
    ids, _ = personas.buscar(nombre or "", directorio)

    if not ids:
        return None, None
    if len(ids) > 1:
        return None, sorted({filas[i]["nombre"] for i in ids})[:8]
    return filas[next(iter(ids))], None
