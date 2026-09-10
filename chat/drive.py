"""Sincroniza una carpeta compartida de Google Drive a un directorio local.

El resto de la app no sabe que existe Drive: `chat/documentos.py` lee una
carpeta de archivos como siempre. Este modulo se encarga de que esa carpeta
sea un espejo de la de Drive, bajando solo lo que cambio.

Acceso: una **cuenta de servicio** con scope `drive.readonly`. La carpeta se
comparte con el email de esa cuenta. No se pide permiso de escritura ni acceso
a todo Drive: la cuenta solo ve lo que le compartieron.

Tipos que entran al corpus:
  - Google Docs nativos  -> se exportan a Markdown.
  - PDF / .txt / .md / .docx subidos -> se bajan tal cual.
  - Google Sheets/Slides, .xlsx y todo lo demas -> se omiten (la planilla de
    cuentas con RUTs se sigue leyendo local, nunca de aca).

Seguridad: cualquiera con permiso de edicion en la carpeta puede dejar un
documento que entra al conocimiento de Iris. Cada documento de texto se pasa
por `chat/antiprompt.py`; si engancha varios patrones de inyeccion no se
indexa y queda como `EventoSeguridad`.
"""

import json
import logging
import os
import re
import threading
from pathlib import Path

import requests
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

SCOPE = "https://www.googleapis.com/auth/drive.readonly"
BASE = "https://www.googleapis.com/drive/v3"

MIME_FOLDER = "application/vnd.google-apps.folder"
MIME_DOC = "application/vnd.google-apps.document"

# Extensiones binarias que el pipeline de documentos entiende.
BINARIAS_OK = {".pdf", ".txt", ".md", ".markdown", ".docx"}
_EXT_POR_MIME = {
    "application/pdf": ".pdf",
    "text/plain": ".txt",
    "text/markdown": ".md",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
}
_TEXTO = {".txt", ".md", ".markdown"}

_MANIFIESTO = ".manifest.json"

_cred_lock = threading.Lock()
_cred_estado = {"ruta": None, "cred": None}


# ---------------------------------------------------------------------------
# Cliente
# ---------------------------------------------------------------------------

def _ruta_credenciales():
    """Acepta ruta absoluta o con ~ (se expande al home del usuario)."""
    return Path(settings.GOOGLE_DRIVE_CREDENTIALS or "").expanduser()


def configurado():
    return bool(
        settings.DOCUMENTOS_FUENTE == "drive"
        and settings.GOOGLE_DRIVE_FOLDER_ID
        and settings.GOOGLE_DRIVE_CREDENTIALS
        and _ruta_credenciales().is_file()
    )


def _credenciales():
    from google.oauth2 import service_account

    ruta = str(_ruta_credenciales())
    if _cred_estado["ruta"] != ruta:
        with _cred_lock:
            if _cred_estado["ruta"] != ruta:
                _cred_estado["cred"] = service_account.Credentials.from_service_account_file(
                    ruta, scopes=[SCOPE])
                _cred_estado["ruta"] = ruta
    return _cred_estado["cred"]


def _token():
    from google.auth.transport.requests import Request

    cred = _credenciales()
    if not cred.valid:
        cred.refresh(Request())
    return cred.token


def _get(path, params, binario=False):
    resp = requests.get(
        f"{BASE}{path}",
        headers={"Authorization": f"Bearer {_token()}"},
        params=params or {},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.content if binario else resp.json()


# ---------------------------------------------------------------------------
# Listado y descarga (aislados para poder mockearlos en los tests)
# ---------------------------------------------------------------------------

def _listar_archivos(folder_id):
    """Todos los archivos (no carpetas) bajo `folder_id`, recursivo."""
    pendientes, vistas, salida = [folder_id], set(), []
    while pendientes:
        actual = pendientes.pop()
        if actual in vistas:
            continue
        vistas.add(actual)
        token = None
        while True:
            params = {
                "q": f"'{actual}' in parents and trashed = false",
                "fields": "nextPageToken, files(id, name, mimeType, modifiedTime)",
                "pageSize": 1000,
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
                "corpora": "allDrives",
            }
            if token:
                params["pageToken"] = token
            data = _get("/files", params)
            for f in data.get("files", []):
                if f.get("mimeType") == MIME_FOLDER:
                    pendientes.append(f["id"])
                else:
                    salida.append(f)
            token = data.get("nextPageToken")
            if not token:
                break
    return salida


def _contenido(archivo):
    """(bytes, extension) para un archivo de Drive, o None si no se indexa."""
    mime = archivo.get("mimeType") or ""
    nombre = archivo.get("name") or ""

    if mime == MIME_DOC:
        return _get(f"/files/{archivo['id']}/export",
                    {"mimeType": "text/markdown"}, binario=True), ".md"

    if mime.startswith("application/vnd.google-apps"):
        return None  # Sheets, Slides, formularios, etc.

    ext = Path(nombre).suffix.lower()
    if ext not in BINARIAS_OK:
        ext = _EXT_POR_MIME.get(mime, "")
    if ext not in BINARIAS_OK:
        return None

    datos = _get(f"/files/{archivo['id']}",
                 {"alt": "media", "supportsAllDrives": "true"}, binario=True)
    return datos, ext


# ---------------------------------------------------------------------------
# Sincronizacion
# ---------------------------------------------------------------------------

def _slug(nombre):
    limpio = re.sub(r"[^\w .()\-]+", "_", nombre, flags=re.UNICODE).strip()
    return re.sub(r"\s+", " ", limpio) or "documento"


def _nombre_local(nombre, ext, fid, usados):
    base = _slug(nombre)
    if base.lower().endswith(ext):
        base = base[: -len(ext)]
    candidato = f"{base}{ext}"
    if usados.get(candidato) not in (None, fid):
        candidato = f"{base}-{fid[:6]}{ext}"
    usados[candidato] = fid
    return candidato


def _sospechoso(datos, ext):
    if ext not in _TEXTO:
        return False  # PDF/.docx se escanean al indexar, no aca
    from .antiprompt import riesgo

    texto = datos.decode("utf-8", "ignore") if isinstance(datos, bytes) else str(datos)
    return riesgo(texto) >= settings.DRIVE_DOC_ANTIPROMPT_UMBRAL


def sincronizar(carpeta, forzar=False):
    """Deja `carpeta` como espejo de la carpeta de Drive. Devuelve un resumen.

    Nunca lanza: si Drive falla, lo anota en `resumen["errores"]` y lo que ya
    estaba sincronizado sigue sirviendo.
    """
    resumen = {"descargados": 0, "omitidos": 0, "borrados": 0,
               "sospechosos": 0, "errores": []}
    if not configurado():
        resumen["errores"].append("Drive no configurado")
        return resumen

    carpeta = Path(carpeta)
    carpeta.mkdir(parents=True, exist_ok=True)
    ruta_manifiesto = carpeta / _MANIFIESTO
    try:
        manifiesto = json.loads(ruta_manifiesto.read_text("utf-8"))
    except (OSError, ValueError):
        manifiesto = {}

    try:
        archivos = _listar_archivos(settings.GOOGLE_DRIVE_FOLDER_ID)
    except Exception as error:  # noqa: BLE001 - se reporta, no se propaga
        logger.warning("no pude listar la carpeta de Drive: %s", error)
        resumen["errores"].append(str(error))
        return resumen

    usados, nuevo, vistos = {}, {}, set()
    for archivo in archivos:
        fid = archivo["id"]
        vistos.add(fid)
        previo = manifiesto.get(fid)

        if (not forzar and previo
                and previo.get("modificado") == archivo.get("modifiedTime")
                and (carpeta / previo["archivo"]).exists()):
            usados[previo["archivo"]] = fid
            nuevo[fid] = previo
            continue

        try:
            contenido = _contenido(archivo)
        except Exception as error:  # noqa: BLE001
            resumen["errores"].append(f"{archivo.get('name')}: {error}")
            if previo:
                nuevo[fid] = previo
                usados[previo["archivo"]] = fid
            continue

        if contenido is None:
            resumen["omitidos"] += 1
            continue

        datos, ext = contenido
        nombre_local = _nombre_local(archivo.get("name") or fid, ext, fid, usados)
        destino = carpeta / nombre_local

        if _sospechoso(datos, ext):
            resumen["sospechosos"] += 1
            from .models import EventoSeguridad, registrar_evento
            registrar_evento(EventoSeguridad.INJECTION, None,
                             f"documento de Drive: {archivo.get('name')}")
            logger.warning("documento de Drive descartado por sospecha de inyeccion: %s",
                           archivo.get("name"))
            if settings.DRIVE_OMITIR_SOSPECHOSOS:
                if destino.exists():
                    destino.unlink()
                continue

        # Reescribir solo si el contenido cambio, para no mover el mtime (y con
        # eso re-particionar y re-embeddear algo identico).
        if not (destino.exists() and destino.read_bytes() == datos):
            tmp = destino.with_name(destino.name + ".tmp")
            tmp.write_bytes(datos)
            os.replace(tmp, destino)
            resumen["descargados"] += 1

        nuevo[fid] = {"archivo": nombre_local,
                      "modificado": archivo.get("modifiedTime"),
                      "nombre": archivo.get("name")}

    for fid, previo in manifiesto.items():
        if fid not in vistos:
            ruta = carpeta / previo["archivo"]
            if ruta.exists():
                ruta.unlink()
            resumen["borrados"] += 1

    ruta_manifiesto.write_text(json.dumps(nuevo, ensure_ascii=False, indent=2), "utf-8")
    return resumen


def sincronizar_si_toca(carpeta):
    """Sincroniza como mucho una vez cada DRIVE_SYNC_TTL, con un lock para que
    dos workers de gunicorn no bajen lo mismo a la vez."""
    if not configurado():
        return
    if cache.get("drive:sync:hecho"):
        return
    if not cache.add("drive:sync:lock", 1, 120):
        return  # otro proceso esta sincronizando ahora
    try:
        resumen = sincronizar(carpeta)
        # En error, backoff corto; en exito, la ventana completa.
        cache.set("drive:sync:hecho", True,
                  60 if resumen["errores"] else settings.DRIVE_SYNC_TTL)
        logger.info("Drive sync: %s", resumen)
    finally:
        cache.delete("drive:sync:lock")
