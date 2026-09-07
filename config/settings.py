import os
import sys
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "dev-only-change-me")
DEBUG = os.getenv("DJANGO_DEBUG", "True").lower() == "true"
def _ip_local():
    """IP de esta maquina en la red local, para servir a otros equipos.

    Se abre un socket UDP sin enviar nada: es la forma portable de saber que
    interfaz usaria el sistema para salir, sin depender de `ifconfig`.
    """
    import socket

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(0.2)
            s.connect(("192.168.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return ""


ALLOWED_HOSTS = [h.strip() for h in
                 os.getenv("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",")
                 if h.strip()]

# En desarrollo se agrega sola la IP de la red local, para no tener que editar
# .env cada vez que el router entrega una direccion distinta.
IP_LOCAL = _ip_local() if DEBUG else ""
if IP_LOCAL and IP_LOCAL not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append(IP_LOCAL)

# El navegador manda Origin en los POST; sin esto el chat falla con 403 CSRF
# cuando se entra por IP en vez de localhost.
CSRF_TRUSTED_ORIGINS = [f"http://{h}:8000" for h in ALLOWED_HOSTS if h not in ("localhost",)]
CSRF_TRUSTED_ORIGINS += [o.strip() for o in
                         os.getenv("DJANGO_CSRF_ORIGINS", "").split(",") if o.strip()]

INSTALLED_APPS = [
    "django.contrib.sessions",   # recuerda una desambiguacion entre mensajes
    "django.contrib.staticfiles",
    "chat",
]

# Carpeta de documentos que el asistente puede consultar (.md / .txt).
DOCUMENTOS_DIR = Path(os.getenv("DOCUMENTOS_DIR", BASE_DIR / "datos"))

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
]

# Hasta cuantas personas se listan por nombre en una respuesta. Mas que esto
# satura el chat y basta el numero.
LISTAR_HASTA = int(os.getenv("LISTAR_HASTA", "25"))

# Cuanto dura una desambiguacion pendiente ("¿cual de las cuatro Javi?").
# Corta a proposito: si el usuario cambia de tema, la siguiente pregunta no
# debe interpretarse como respuesta a algo que ya olvido.
DESAMBIGUACION_SEGUNDOS = int(os.getenv("DESAMBIGUACION_SEGUNDOS", "180"))

ROOT_URLCONF = "config.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": []},
    },
]
WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

LANGUAGE_CODE = "es-cl"
TIME_ZONE = "America/Santiago"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Por defecto en base de datos: sobrevive a los reinicios y lo comparten todos
# los procesos. Con LocMemCache, cada reinicio del servidor vuelve a pedirle
# todo a BUK. Para cambiarlo: CACHE_BACKEND=locmem
if os.getenv("CACHE_BACKEND", "db").lower() == "locmem":
    CACHES = {"default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "buk",
    }}
else:
    CACHES = {"default": {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
        "LOCATION": "cache_respuestas",
    }}

BUK_API_KEY = os.getenv("BUK_API_KEY", "")
BUK_API_BASE = os.getenv("BUK_API_BASE", "https://azerta.buk.cl/api/v1/chile").rstrip("/")
BUK_AUTH_HEADER = os.getenv("BUK_AUTH_HEADER", "auth_token")
BUK_AUTH_PREFIX = os.getenv("BUK_AUTH_PREFIX", "")
BUK_TIMEOUT = int(os.getenv("BUK_TIMEOUT", "10"))

# 100 es el maximo util por pagina en /absences; 200 alcanza para toda la nomina.
BUK_PAGE_SIZE = int(os.getenv("BUK_PAGE_SIZE", "100"))
BUK_DIRECTORY_PAGE_SIZE = int(os.getenv("BUK_DIRECTORY_PAGE_SIZE", "200"))
BUK_CACHE_TTL = int(os.getenv("BUK_CACHE_TTL", "600"))
BUK_ABSENCE_CACHE_TTL = int(os.getenv("BUK_ABSENCE_CACHE_TTL", "60"))

# /vacations no filtra por rango: su parametro `date` devuelve las que empiezan
# desde esa fecha. Se pide con este margen hacia atras para no perder ninguna en
# curso; la vacacion mas larga registrada dura 58 dias.
BUK_VACACIONES_MARGEN = timedelta(days=int(os.getenv("BUK_VACACIONES_MARGEN_DIAS", "120")))

# Modelo de lenguaje. Se usa solo como respaldo del router de reglas: sin clave,
# la aplicacion funciona igual y responde "no tengo esa informacion".
# ASISTENTE_PROVEEDOR: "gemini" (tiene capa gratuita) u "openai".
ASISTENTE_PROVEEDOR = os.getenv("ASISTENTE_PROVEEDOR", "gemini").lower()
ASISTENTE_TIMEOUT = int(os.getenv("ASISTENTE_TIMEOUT", "18"))

# Si el proveedor falla varias veces seguidas se deja de llamar por un rato y
# responden las reglas al instante. Se reactiva solo al vencer la pausa.
ASISTENTE_FALLAS_MAX = int(os.getenv("ASISTENTE_FALLAS_MAX", "3"))
ASISTENTE_PAUSA_SEGUNDOS = int(os.getenv("ASISTENTE_PAUSA_SEGUNDOS", "180"))
ASISTENTE_MAX_PASOS = int(os.getenv("ASISTENTE_MAX_PASOS", "4"))

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

# Ningun test debe llamar a la API ni gastar tokens ni cuota. Los tests del
# asistente inyectan una clave falsa con override_settings y simulan el cliente.
if "test" in sys.argv:
    GEMINI_API_KEY = ""
    OPENAI_API_KEY = ""

# True = todas las preguntas pasan por el modelo (responde mejor, cuesta mas).
# False = el modelo solo atiende lo que las reglas no entienden.
ASISTENTE_SIEMPRE = os.getenv("ASISTENTE_SIEMPRE", "False").lower() == "true"

# Reemplaza los nombres de la nomina por alias antes de mandarlos al modelo y
# los restituye en la respuesta. No protege el nombre que el usuario escribio.
ASISTENTE_ANONIMIZAR = os.getenv("ASISTENTE_ANONIMIZAR", "False").lower() == "true"

# Busqueda semantica sobre los documentos. Opcional: sin clave o si la API
# falla, queda solo la busqueda lexica y la app responde igual.
EMBEDDINGS_ACTIVOS = os.getenv("EMBEDDINGS_ACTIVOS", "True").lower() == "true"
EMBEDDINGS_MODELO = os.getenv("EMBEDDINGS_MODELO", "gemini-embedding-001")
EMBEDDINGS_DIMENSIONES = int(os.getenv("EMBEDDINGS_DIMENSIONES", "768"))
EMBEDDINGS_LOTE = int(os.getenv("EMBEDDINGS_LOTE", "20"))
EMBEDDINGS_MAX_CARACTERES = int(os.getenv("EMBEDDINGS_MAX_CARACTERES", "6000"))
# La capa gratuita permite 100 elementos por minuto. Se deja margen para no
# chocar con el limite; subir esto solo tiene sentido con plan de pago.
EMBEDDINGS_RPM = int(os.getenv("EMBEDDINGS_RPM", "85"))
EMBEDDINGS_REINTENTOS = int(os.getenv("EMBEDDINGS_REINTENTOS", "4"))
EMBEDDINGS_ARCHIVO = Path(os.getenv("EMBEDDINGS_ARCHIVO", BASE_DIR / ".embeddings.json"))

# Peso de la busqueda semantica frente a la lexica al combinarlas. La lexica
# gana cuando la pregunta usa el termino exacto; la semantica, cuando lo
# parafrasea. Usar solo una de las dos es peor que mezclarlas.
EMBEDDINGS_PESO = float(os.getenv("EMBEDDINGS_PESO", "0.85"))

# Ningun test debe pedir embeddings a la API. Va aca y no en el bloque de mas
# arriba porque la variable se define despues y lo sobrescribiria.
if "test" in sys.argv:
    EMBEDDINGS_ACTIVOS = False

# Cuando nadie cumple anos en la fecha preguntada, se mira hasta aca adelante
# para poder decir a quien hay que saludar pronto.
CUMPLE_HORIZONTE_DIAS = int(os.getenv("CUMPLE_HORIZONTE_DIAS", "45"))

# Segundos que se guarda la respuesta completa a una pregunta. Repetirla dentro
# de esta ventana no consulta BUK ni gasta tokens.
RESPUESTA_CACHE_TTL = int(os.getenv("RESPUESTA_CACHE_TTL", "600"))
