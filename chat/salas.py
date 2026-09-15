"""Salas de reuniones: disponibilidad y reserva en Google Calendar.

Dos APIs de Google, con la MISMA cuenta de servicio con delegacion de dominio
(ver settings.GOOGLE_CALENDAR_CREDENTIALS), pero "actuando como" alguien
distinto segun la llamada:

- Para listar las salas (Admin SDK Directory, resources.calendars) hace falta
  actuar como alguien con privilegios de administrador: se usa
  settings.GOOGLE_WORKSPACE_ADMIN. Se cachea (CACHE_TTL): agregar o sacar una
  sala del Workspace es un evento raro, no vale la pena pedirlo en cada turno.
- Para ver disponibilidad y crear una reunion (Calendar API) actua como quien
  esta preguntando (su cuenta de Google, la que inicio sesion): la reunion
  queda organizada por esa persona, no por un bot generico, igual que si la
  hubiera creado ella misma a mano en Calendar.

Sin esto, quien recibe visitas sigue mirando sala por sala en Calendar para
armar una reunion a mano.
"""

import logging
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

ZONA = ZoneInfo("America/Santiago")

SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.freebusy",
    "https://www.googleapis.com/auth/admin.directory.resource.calendar.readonly",
]

CACHE_SALAS = "salas:directorio:v1"
CACHE_TTL = 6 * 60 * 60  # 6 horas, igual criterio que buk.directorio


class SalasError(Exception):
    """Error al hablar con Calendar/Directory, con mensaje listo para el usuario."""


# ---------------------------------------------------------------------------
# Credenciales
# ---------------------------------------------------------------------------

def _ruta_credenciales():
    """Acepta ruta absoluta o con ~ (se expande al home del usuario)."""
    return Path(settings.GOOGLE_CALENDAR_CREDENTIALS or "").expanduser()


def configurado():
    return bool(
        settings.GOOGLE_CALENDAR_CREDENTIALS
        and settings.GOOGLE_WORKSPACE_ADMIN
        and _ruta_credenciales().is_file()
    )


def _credenciales_base():
    from google.oauth2 import service_account

    return service_account.Credentials.from_service_account_file(
        str(_ruta_credenciales()), scopes=SCOPES)


def _credenciales(subject):
    """Credenciales delegadas actuando como `subject` (un correo @azerta.cl).

    `with_subject` no hace ninguna llamada de red: arma una copia liviana de
    las credenciales base, que se autentica sola en la primera request.
    """
    return _credenciales_base().with_subject(subject)


# ---------------------------------------------------------------------------
# Directorio de salas (Admin SDK Directory API)
# ---------------------------------------------------------------------------

def _es_sala_de_reuniones(recurso):
    """Filtra salas de reuniones de otros recursos del Workspace
    (estacionamientos, equipos, etc).

    `resourceCategory` es el campo pensado para esto ("CONFERENCE_ROOM"), pero
    un recurso creado hace tiempo puede no tenerlo seteado: para esos, se cae
    al texto libre de `resourceType` (lo que se ve en la ficha de la sala en
    Calendar, ej. "Sala de Reuniones").
    """
    categoria = (recurso.get("resourceCategory") or "").upper()
    if categoria:
        return categoria == "CONFERENCE_ROOM"
    tipo = (recurso.get("resourceType") or "").lower()
    return "sala" in tipo or "reunion" in tipo or "reunión" in tipo


def _directorio(forzar=False):
    """Lista de salas de reuniones: [{"email", "nombre", "capacidad"}, ...]."""
    if not forzar:
        cacheada = cache.get(CACHE_SALAS)
        if cacheada is not None:
            return cacheada

    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError

    if not configurado():
        raise SalasError(
            "La reserva de salas no esta configurada todavia. Avisa al equipo técnico.")

    try:
        servicio = build("admin", "directory_v1",
                         credentials=_credenciales(settings.GOOGLE_WORKSPACE_ADMIN))
        respuesta = servicio.resources().calendars().list(customer="my_customer").execute()
    except HttpError as error:
        raise SalasError(f"No pude consultar las salas en Google (HTTP {error.status_code}).")

    salas = sorted(
        (
            {
                "email": r["resourceEmail"],
                "nombre": (r.get("resourceName") or r["resourceEmail"]).strip(),
                "capacidad": r.get("capacity"),
            }
            for r in respuesta.get("items", [])
            if r.get("resourceEmail") and _es_sala_de_reuniones(r)
        ),
        key=lambda s: s["nombre"],
    )
    cache.set(CACHE_SALAS, salas, CACHE_TTL)
    return salas


# ---------------------------------------------------------------------------
# Disponibilidad y reserva (Calendar API)
# ---------------------------------------------------------------------------

def _rango(fecha, hora_inicio, hora_fin):
    try:
        d = date.fromisoformat(fecha)
        hi = datetime.strptime(hora_inicio, "%H:%M").time()
        hf = datetime.strptime(hora_fin, "%H:%M").time()
    except (TypeError, ValueError):
        raise SalasError(
            "La fecha o el horario no tienen el formato esperado (AAAA-MM-DD y HH:MM).")
    inicio = datetime.combine(d, hi, tzinfo=ZONA)
    fin = datetime.combine(d, hf, tzinfo=ZONA)
    if fin <= inicio:
        raise SalasError("La hora de término tiene que ser después de la de inicio.")
    return inicio, fin


def _ocupadas(servicio, correos, inicio, fin):
    """{correo: True/False} segun si tiene algun bloque ocupado en el rango."""
    from googleapiclient.errors import HttpError

    try:
        respuesta = servicio.freebusy().query(body={
            "timeMin": inicio.isoformat(),
            "timeMax": fin.isoformat(),
            "items": [{"id": correo} for correo in correos],
        }).execute()
    except HttpError as error:
        raise SalasError(f"No pude consultar la disponibilidad en Calendar (HTTP {error.status_code}).")

    calendarios = respuesta.get("calendars", {})
    return {correo: bool((calendarios.get(correo) or {}).get("busy"))
            for correo in correos}


def disponibilidad(organizador_email, fecha, hora_inicio, hora_fin):
    """[{"sala", "ocupada"}, ...] para cada sala de reuniones, en ese rango.

    Se consulta "actuando como" `organizador_email`: ver a que hora esta libre
    una sala no pide privilegios especiales, lo puede ver cualquiera en
    Calendar, asi que no hace falta el admin aca (solo para listar las salas).
    """
    from googleapiclient.discovery import build

    inicio, fin = _rango(fecha, hora_inicio, hora_fin)
    salas = _directorio()
    if not salas:
        raise SalasError("No hay salas de reuniones registradas en Google Workspace.")

    servicio = build("calendar", "v3", credentials=_credenciales(organizador_email))
    ocupadas = _ocupadas(servicio, [s["email"] for s in salas], inicio, fin)
    return [{"sala": s["nombre"], "ocupada": ocupadas.get(s["email"], False)} for s in salas]


def crear_reunion(organizador_email, sala_nombre, fecha, hora_inicio, hora_fin, titulo,
                  invitados=None):
    """Reserva `sala_nombre` y crea el evento en el calendario de `organizador_email`.

    Vuelve a chequear disponibilidad pegado a la creacion (no confia en una
    consulta de `disponibilidad` de hace un rato): entre que se mostro la
    lista y la persona eligio, alguien mas pudo haber reservado esa sala.
    """
    from googleapiclient.discovery import build

    inicio, fin = _rango(fecha, hora_inicio, hora_fin)
    sala = next((s for s in _directorio()
                if s["nombre"].strip().lower() == (sala_nombre or "").strip().lower()), None)
    if sala is None:
        return {"creada": False, "motivo": "sala_desconocida"}

    servicio = build("calendar", "v3", credentials=_credenciales(organizador_email))
    if _ocupadas(servicio, [sala["email"]], inicio, fin)[sala["email"]]:
        return {"creada": False, "motivo": "ocupada"}

    from googleapiclient.errors import HttpError

    cuerpo = {
        "summary": titulo,
        "start": {"dateTime": inicio.isoformat(), "timeZone": "America/Santiago"},
        "end": {"dateTime": fin.isoformat(), "timeZone": "America/Santiago"},
        "attendees": [{"email": sala["email"], "resource": True}] + [
            {"email": correo} for correo in (invitados or []) if correo
        ],
    }
    try:
        evento = servicio.events().insert(
            calendarId="primary", body=cuerpo, sendUpdates="all").execute()
    except HttpError as error:
        raise SalasError(f"No pude crear la reunión en Calendar (HTTP {error.status_code}).")

    return {"creada": True, "sala": sala["nombre"], "link": evento.get("htmlLink")}
