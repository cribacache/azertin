"""Tarjeta de cumpleanos: la foto de la persona sobre el template de Azerta,
mandada por Gmail al equipo de Personas.

La foto sale de chat/fotos_equipo.py (azerta.cl/equipo, foto profesional
curada para el sitio publico) y, si no esta ahi, de BUK
(persona["_picture_url"], la foto del legajo -ver chat/buk.py). Si ninguna de
las dos tiene nada, se avisa (SinFoto) en vez de mandar una tarjeta vacia.

El envio usa Gmail API con una cuenta de servicio delegada en todo el
dominio de azerta.cl (ver settings.GOOGLE_GMAIL_CREDENTIALS): la cuenta de
servicio manda "como" GOOGLE_GMAIL_REMITENTE, sin SMTP ni contraseña de
aplicacion.

Las coordenadas del circulo y del recuadro del nombre estan medidas a mano
sobre chat/assets/cumpleanos/template.png (2500x2500 px): si el diseño
cambia hay que volver a medirlas (por ejemplo abriendo el PNG y mirando en
que pixeles empieza y termina el circulo azul de relleno).
"""

import base64
import logging
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from io import BytesIO
from pathlib import Path

import requests
from django.conf import settings
from PIL import Image, ImageDraw, ImageFont, ImageOps

from . import fotos_equipo

logger = logging.getLogger(__name__)

ASSETS = Path(__file__).resolve().parent / "assets" / "cumpleanos"
TEMPLATE = ASSETS / "template.png"
FUENTE = ASSETS / "Caveat.ttf"

# Medido sobre template.png (2500x2500). Ver docstring del modulo.
CIRCULO_CENTRO = (1249, 931)
CIRCULO_RADIO = 631
NOMBRE_BBOX = (884, 1941, 1616, 2087)  # x0, y0, x1, y1 del "(Nombre)" original
NOMBRE_COLOR = (30, 30, 90)
NOMBRE_TAM_MAX = 120
NOMBRE_TAM_MIN = 40


class SinFoto(Exception):
    """Ni azerta.cl/equipo ni BUK tienen una foto para esta persona."""


def _foto_de(persona):
    url = fotos_equipo.url_de(persona["nombre"]) or persona.get("_picture_url")
    if not url:
        raise SinFoto(f"sin foto para {persona['nombre']}")
    respuesta = requests.get(url, timeout=15)
    respuesta.raise_for_status()
    return Image.open(BytesIO(respuesta.content)).convert("RGB")


def _nombre_corto(persona):
    """Primer nombre + primer apellido para la tarjeta.

    persona["nombre"] es el full_name que arma BUK y puede traer nombre
    compuesto ("Irene Maria Cobo Paris"): tomar sus primeras dos palabras da
    "Irene Maria" (le falta el apellido). Se prefiere persona["_nombre_pila"]
    / ["_apellido"] (campos separados de BUK, ver chat/buk.py) y solo se cae
    al split ingenuo si no estan (por ejemplo en un dict de prueba minimo).
    """
    pila, apellido = persona.get("_nombre_pila"), persona.get("_apellido")
    if pila and apellido:
        return f"{pila.split()[0]} {apellido.split()[0]}"
    return " ".join(persona["nombre"].split()[:2])


def _dibujar_nombre(draw, nombre):
    x0, y0, x1, y1 = NOMBRE_BBOX
    pad = 20
    draw.rectangle((x0 - pad, y0 - pad, x1 + pad, y1 + pad), fill=(255, 255, 255))

    alto_objetivo = y1 - y0
    tam = NOMBRE_TAM_MAX
    fuente = ImageFont.truetype(str(FUENTE), tam)
    bbox = draw.textbbox((0, 0), nombre, font=fuente)
    while (bbox[3] - bbox[1]) > alto_objetivo * 1.3 and tam > NOMBRE_TAM_MIN:
        tam -= 4
        fuente = ImageFont.truetype(str(FUENTE), tam)
        bbox = draw.textbbox((0, 0), nombre, font=fuente)

    ancho_texto, alto_texto = bbox[2] - bbox[0], bbox[3] - bbox[1]
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    pos = (cx - ancho_texto // 2 - bbox[0], cy - alto_texto // 2 - bbox[1])
    draw.text(pos, nombre, font=fuente, fill=NOMBRE_COLOR)


def tarjeta(persona):
    """PNG (bytes) con la foto y el nombre de `persona` sobre el template.

    Lanza SinFoto si no hay foto en ninguna de las dos fuentes; deja
    propagar cualquier error de red (descarga de la foto) sin silenciarlo.
    """
    template = Image.open(TEMPLATE).convert("RGB")
    foto = _foto_de(persona)

    diametro = CIRCULO_RADIO * 2
    # centering=(0.5, 0.35): la mayoria de las fotos de perfil dejan mas aire
    # arriba de la cabeza que abajo del mentom; recortar desde el centro
    # geometrico corta la coronilla en fotos de cuerpo entero.
    foto_cuadrada = ImageOps.fit(foto, (diametro, diametro), method=Image.LANCZOS,
                                 centering=(0.5, 0.35))
    mascara = Image.new("L", (diametro, diametro), 0)
    ImageDraw.Draw(mascara).ellipse((0, 0, diametro, diametro), fill=255)
    esquina = (CIRCULO_CENTRO[0] - CIRCULO_RADIO, CIRCULO_CENTRO[1] - CIRCULO_RADIO)
    template.paste(foto_cuadrada, esquina, mascara)

    _dibujar_nombre(ImageDraw.Draw(template), _nombre_corto(persona))

    salida = BytesIO()
    template.save(salida, format="PNG")
    return salida.getvalue()


def _credenciales_gmail():
    from google.oauth2 import service_account

    ruta = Path(settings.GOOGLE_GMAIL_CREDENTIALS or "").expanduser()
    return service_account.Credentials.from_service_account_file(
        str(ruta), scopes=["https://www.googleapis.com/auth/gmail.send"],
    ).with_subject(settings.GOOGLE_GMAIL_REMITENTE)


def _enviar_correo(destinatario, asunto, cuerpo, imagen_png, nombre_archivo):
    from googleapiclient.discovery import build

    mensaje = MIMEMultipart()
    mensaje["to"] = destinatario
    mensaje["from"] = settings.GOOGLE_GMAIL_REMITENTE
    mensaje["subject"] = asunto
    mensaje.attach(MIMEText(cuerpo))
    mensaje.attach(MIMEImage(imagen_png, name=nombre_archivo))

    crudo = base64.urlsafe_b64encode(mensaje.as_bytes()).decode()
    servicio = build("gmail", "v1", credentials=_credenciales_gmail())
    servicio.users().messages().send(userId="me", body={"raw": crudo}).execute()


def enviar_tarjeta(persona):
    """Arma la tarjeta de `persona` y la manda a settings.GOOGLE_GMAIL_DESTINO.

    No atrapa errores: el llamador (el management command) decide como
    reportar un SinFoto o una falla de red/Gmail, no se silencian aca.
    """
    imagen = tarjeta(persona)
    nombre_archivo = f"cumpleanos-{_nombre_corto(persona).replace(' ', '-').lower()}.png"
    _enviar_correo(
        destinatario=settings.GOOGLE_GMAIL_DESTINO,
        asunto=f"Cumpleaños de hoy: {persona['nombre']}",
        cuerpo=f"Adjunto la tarjeta de cumpleaños de {persona['nombre']} para hoy.",
        imagen_png=imagen,
        nombre_archivo=nombre_archivo,
    )
