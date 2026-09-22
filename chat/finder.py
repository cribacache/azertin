"""Azerta Finder: numero de contacto de cada persona, desde un archivo de
Drive (un .xlsx) compartido DIRECTO con la cuenta de servicio -no vive
dentro de la carpeta que chat/drive.py sincroniza, asi que se lee por su id,
no por nombre dentro de esa carpeta ni via DRIVE_HOJAS_PERMITIDAS.

Acceso restringido por lista de correos (settings.AZERTA_FINDER_USUARIOS),
mismo criterio que chat/salas.py::usuario_habilitado: es informacion de
contacto de terceros, confidencial, y se abre persona por persona, sin
excepcion por rol -ni gerencia ni un superusuario entran si no estan en la
lista.
"""

import io
import logging

from django.conf import settings
from django.core.cache import cache

from .intents import normalizar

logger = logging.getLogger(__name__)

CACHE_FILAS = "finder:filas:v2"

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

    Cacheadas con TTL fijo (settings.AZERTA_FINDER_CACHE_TTL): a diferencia
    de la carpeta sincronizada, no hay un archivo local cuyo mtime avise que
    cambio, asi que no vale la pena distinguir "cache" de "descarga de
    verdad" -se vuelve a descargar entero cuando vence, nada mas. Lanza
    FinderError si el archivo no esta configurado o no se pudo leer, para que
    la herramienta que llama pueda avisar en vez de decir "no encontrada" por
    un problema que no tiene nada que ver con el nombre buscado.
    """
    if not forzar:
        cacheado = cache.get(CACHE_FILAS)
        if cacheado is not None:
            return cacheado

    file_id = getattr(settings, "AZERTA_FINDER_FILE_ID", "")
    if not file_id or not settings.GOOGLE_DRIVE_CREDENTIALS:
        raise FinderError("Azerta Finder no está disponible todavía. Avisa al equipo técnico.")

    from . import drive

    try:
        contenido = drive.descargar_archivo(file_id)
    except Exception as error:
        logger.warning("azerta finder: no pude descargar la planilla: %s", error)
        raise FinderError("No pude leer la planilla de contactos en este momento.")

    try:
        import openpyxl
    except ImportError:
        raise FinderError("Azerta Finder no está disponible todavía. Avisa al equipo técnico.")

    try:
        libro = openpyxl.load_workbook(io.BytesIO(contenido), data_only=True, read_only=True)
        hoja = libro[libro.sheetnames[0]]
        crudas = list(hoja.iter_rows(values_only=True))
        libro.close()
    except Exception as error:
        logger.warning("azerta finder: no se pudo leer la planilla: %s", error)
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

    cache.set(CACHE_FILAS, filas, settings.AZERTA_FINDER_CACHE_TTL)
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
