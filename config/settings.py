import os
import sys
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "dev-only-change-me")
DEBUG = os.getenv("DJANGO_DEBUG", "True").lower() == "true"
ALLOWED_HOSTS = [host.strip() for host in os.getenv("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",") if host.strip()]

INSTALLED_APPS = [
    "django.contrib.staticfiles",
    "chat",
]

# Carpeta de documentos que el asistente puede consultar (.md / .txt).
DOCUMENTOS_DIR = Path(os.getenv("DOCUMENTOS_DIR", BASE_DIR / "datos"))

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
]

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
ASISTENTE_TIMEOUT = int(os.getenv("ASISTENTE_TIMEOUT", "30"))
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

# Segundos que se guarda la respuesta completa a una pregunta. Repetirla dentro
# de esta ventana no consulta BUK ni gasta tokens.
RESPUESTA_CACHE_TTL = int(os.getenv("RESPUESTA_CACHE_TTL", "600"))
