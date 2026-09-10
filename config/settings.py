import os
import re
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

# Origenes de confianza para el chequeo de CSRF (Django compara el header
# Origin/Referer contra esta lista en cada POST).
#
# En produccion se define explicito por env, CON scheme y puerto:
#   DJANGO_CSRF_ORIGINS=https://iris.azerta.cl
# El fallback http://<host>:8000 es solo para desarrollo (entrar por IP de la
# red local sin editar nada); en produccion sobre HTTPS seria un origen
# inseguro de confianza, asi que solo se agrega con DEBUG.
CSRF_TRUSTED_ORIGINS = [o.strip() for o in
                        os.getenv("DJANGO_CSRF_ORIGINS", "").split(",") if o.strip()]
if DEBUG:
    CSRF_TRUSTED_ORIGINS += [f"http://{h}:8000" for h in ALLOWED_HOSTS
                             if h not in ("localhost",)]

INSTALLED_APPS = [
    # Los cuatro de abajo son solo para /admin/: ahi se trabaja el backlog de
    # ConsultaNoResuelta (que pregunta quedo sin poder responderse) ordenado
    # por cuantas veces se repitio, en vez de a mano por la shell.
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.messages",
    "django.contrib.sessions",   # recuerda una desambiguacion entre mensajes
    "django.contrib.staticfiles",
    # Login con Google: django-allauth pide "sites" aunque no se use la
    # configuracion de SocialApp por base de datos (ver GOOGLE_OAUTH mas abajo).
    "django.contrib.sites",
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "allauth.socialaccount.providers.google",
    "chat",
]

SITE_ID = 1

# Carpeta local de documentos que el asistente puede consultar. Sigue siendo
# la fuente de la planilla de cuentas (chat/cuentas.py, con RUTs) y el respaldo
# de los documentos de politica si DOCUMENTOS_FUENTE queda en "local".
DOCUMENTOS_DIR = Path(os.getenv("DOCUMENTOS_DIR", BASE_DIR / "datos"))

# ---------------------------------------------------------------------------
# Origen de los documentos de politica (NO de la planilla de cuentas, que
# siempre se lee de DOCUMENTOS_DIR local).
#
#   local  -> los archivos en DOCUMENTOS_DIR, como siempre.
#   drive  -> una carpeta compartida de Google Drive, que chat/drive.py
#             sincroniza a DRIVE_CACHE_DIR con una cuenta de servicio de solo
#             lectura. El resto del pipeline (particion, embeddings, busqueda)
#             no cambia: lee de esa carpeta local.
# ---------------------------------------------------------------------------
DOCUMENTOS_FUENTE = os.getenv("DOCUMENTOS_FUENTE", "local").strip().lower()

# Acepta el ID pelado o la URL completa de la carpeta ("../folders/<ID>?...").
_drive_folder = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "").strip()
_m = re.search(r"folders/([A-Za-z0-9_-]+)", _drive_folder)
GOOGLE_DRIVE_FOLDER_ID = _m.group(1) if _m else _drive_folder

# Ruta al JSON de la cuenta de servicio (scope drive.readonly). La carpeta se
# comparte con el email de esa cuenta.
GOOGLE_DRIVE_CREDENTIALS = os.getenv("GOOGLE_DRIVE_CREDENTIALS", "").strip()

DRIVE_CACHE_DIR = Path(os.getenv("DRIVE_CACHE_DIR", BASE_DIR / ".drive_cache"))
# Cada cuanto se vuelve a mirar Drive (solo baja lo que cambio).
DRIVE_SYNC_TTL = int(os.getenv("DRIVE_SYNC_TTL", "300"))
# Un documento de Drive que enganche patrones de inyeccion no entra al corpus
# y queda como EventoSeguridad. Umbral mas alto que el del chat (una politica
# real puede decir "no esta permitido..."), pide varios patrones distintos.
DRIVE_OMITIR_SOSPECHOSOS = os.getenv("DRIVE_OMITIR_SOSPECHOSOS", "True").lower() == "true"
DRIVE_DOC_ANTIPROMPT_UMBRAL = int(os.getenv("DRIVE_DOC_ANTIPROMPT_UMBRAL", "2"))

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # Cabeceras de seguridad que Django no pone solo: CSP, Permissions-Policy,
    # Cross-Origin-Resource-Policy. Arriba del todo para que alcancen tambien
    # a las respuestas de error y a los redirect de login.
    "chat.middleware.SecurityHeadersMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    # Frena el fuerza-bruta contra el login del admin (usuario/clave, la unica
    # puerta que no pasa por Google y que ve TODAS las filas).
    "chat.middleware.AdminBruteForceMiddleware",
    # Al final: ya paso auth (sabe quien es request.user) y allauth (ya
    # resolvio sus propias URLs). Exige sesion iniciada para todo lo demas.
    "chat.middleware.RequiereLoginMiddleware",
    # Clickjacking: sin esto no habia X-Frame-Options.
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

X_FRAME_OPTIONS = "DENY"

# ---------------------------------------------------------------------------
# Endurecimiento para produccion.
#
# Se activa solo con DEBUG=False. En dev (HTTP plano, entrar por IP) estas
# opciones romperian el acceso, por eso quedan atadas a !DEBUG y algunas
# ademas a su propia variable de entorno.
# ---------------------------------------------------------------------------
SECURE_CONTENT_TYPE_NOSNIFF = True          # X-Content-Type-Options: nosniff
SECURE_REFERRER_POLICY = "same-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"

if not DEBUG:
    # Cookies solo por HTTPS.
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    # El front lee el token CSRF del <input> del formulario, no de la cookie
    # (ver static/chat/app.js), asi que la cookie puede ser HttpOnly.
    CSRF_COOKIE_HTTPONLY = True
    SESSION_COOKIE_HTTPONLY = True

    # Redirige HTTP -> HTTPS. Si hay un proxy/balanceador que termina TLS,
    # ademas hay que decirle a Django como reconocer el request original:
    #   DJANGO_BEHIND_TLS_PROXY=True
    SECURE_SSL_REDIRECT = os.getenv("DJANGO_SSL_REDIRECT", "True").lower() == "true"
    if os.getenv("DJANGO_BEHIND_TLS_PROXY", "False").lower() == "true":
        SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

    # HSTS: el navegador recuerda "este dominio es solo HTTPS" por N segundos.
    # OJO: si lo activas y despues no podes servir HTTPS, el navegador se niega
    # a entrar. Empeza con un valor chico (p. ej. 3600), confirma que todo anda
    # y recien ahi subilo. 0 = desactivado.
    SECURE_HSTS_SECONDS = int(os.getenv("DJANGO_HSTS_SECONDS", "0"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = os.getenv(
        "DJANGO_HSTS_SUBDOMAINS", "False").lower() == "true"
    SECURE_HSTS_PRELOAD = os.getenv("DJANGO_HSTS_PRELOAD", "False").lower() == "true"

# Rate limit del login del admin (chat/middleware.py::AdminBruteForceMiddleware).
RATE_LIMIT_ADMIN_LOGIN = os.getenv("RATE_LIMIT_ADMIN_LOGIN", "10/300")

ROOT_URLCONF = "config.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": [
            # Los tres que pide el admin de Django para dibujar su interfaz.
            "django.template.context_processors.request",
            "django.contrib.auth.context_processors.auth",
            "django.contrib.messages.context_processors.messages",
            "chat.context_processors.dominio_google",
        ]},
    },
]
WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
        "OPTIONS": {
            # WAL: los lectores no esperan a que termine una escritura (el modo
            # por defecto de SQLite bloquea todo el archivo mientras alguien
            # escribe). Con varias personas preguntando a la vez, sin esto cada
            # `registrar()`/`contar()` podria demorar a los demas.
            "init_command": "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;",
            # Toma el lock de escritura al abrir la transaccion, no a mitad de
            # camino: evita el "database is locked" que da el modo por defecto
            # cuando dos escrituras casi se cruzan.
            "transaction_mode": "IMMEDIATE",
        },
    }
}

# ---------------------------------------------------------------------------
# Login: solo Google, solo cuentas del dominio de Azerta.
#
# Sin usuario y clave propios (ACCOUNT_ADAPTER en chat/adapters.py cierra el
# registro local): la unica puerta es una cuenta de Google del dominio de
# abajo. El parametro `hd` que ve Google en su pantalla es solo una
# sugerencia visual, se puede evitar eligiendo otra cuenta ya logueada en el
# navegador - la barrera real es del lado del servidor, en
# SoloAzertaSocialAdapter.pre_social_login.
# ---------------------------------------------------------------------------

AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]

LOGIN_URL = "account_login"
LOGIN_REDIRECT_URL = "/"
ACCOUNT_LOGOUT_REDIRECT_URL = "account_login"
# Google ya valida el email antes de entregarlo: no hace falta el correo de
# confirmacion propio de allauth para cuentas locales.
ACCOUNT_EMAIL_VERIFICATION = "none"

ACCOUNT_ADAPTER = "chat.adapters.SoloAzertaAccountAdapter"
SOCIALACCOUNT_ADAPTER = "chat.adapters.SoloAzertaSocialAdapter"

GOOGLE_WORKSPACE_DOMAIN = os.getenv("GOOGLE_WORKSPACE_DOMAIN", "azerta.cl")

SOCIALACCOUNT_PROVIDERS = {
    "google": {
        "APP": {
            "client_id": os.getenv("GOOGLE_OAUTH_CLIENT_ID", ""),
            "secret": os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", ""),
            "key": "",
        },
        "SCOPE": ["email", "profile"],
        # access_type=online: no se necesita un refresh token, esto no vuelve
        # a llamar a la API de Google despues del login.
        "AUTH_PARAMS": {"access_type": "online", "hd": GOOGLE_WORKSPACE_DOMAIN},
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

# Modelo de lenguaje: solo Gemini, con presupuesto aprobado (antes existia un
# respaldo con OpenAI mientras se evaluaba; se saco al confirmarse el pago).
ASISTENTE_TIMEOUT = int(os.getenv("ASISTENTE_TIMEOUT", "18"))

# Si el proveedor falla varias veces seguidas se deja de llamar por un rato y
# responden las reglas al instante. Se reactiva solo al vencer la pausa.
ASISTENTE_FALLAS_MAX = int(os.getenv("ASISTENTE_FALLAS_MAX", "3"))
ASISTENTE_PAUSA_SEGUNDOS = int(os.getenv("ASISTENTE_PAUSA_SEGUNDOS", "180"))
ASISTENTE_MAX_PASOS = int(os.getenv("ASISTENTE_MAX_PASOS", "4"))

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")

# Ningun test debe llamar a la API ni gastar tokens ni cuota. Los tests del
# asistente inyectan una clave falsa con override_settings y simulan el cliente.
if "test" in sys.argv:
    GEMINI_API_KEY = ""

# Reemplaza los nombres de la nomina por alias antes de mandarlos al modelo y
# los restituye en la respuesta. No protege el nombre que el usuario escribio.
# Activado por defecto: es PII de terceros yendo a un proveedor externo.
# Para volver a mandar nombres reales: ASISTENTE_ANONIMIZAR=False
ASISTENTE_ANONIMIZAR = os.getenv("ASISTENTE_ANONIMIZAR", "True").lower() == "true"

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

# Segundos que se guarda la respuesta completa a una pregunta. Repetirla dentro
# de esta ventana no consulta BUK ni gasta tokens.
RESPUESTA_CACHE_TTL = int(os.getenv("RESPUESTA_CACHE_TTL", "600"))

# ---------------------------------------------------------------------------
# Seguridad: autorizacion por rol, limites de uso y anti prompt-injection.
#
# Todo esto se apaga bajo `test` (mismo patron que GEMINI_API_KEY): la suite
# vieja no prueba autorizacion, y las clases nuevas lo prenden con
# override_settings donde corresponde.
# ---------------------------------------------------------------------------

# Filtra lo que cada rol puede ver (chat/autorizacion.py). Con esto en False,
# todos preguntan como gerencia.
AUTORIZACION_ACTIVA = os.getenv("AUTORIZACION_ACTIVA", "True").lower() == "true"

# Limite de frecuencia (chat/ratelimit.py). Formato "N/segundos".
RATE_LIMIT_ACTIVO = os.getenv("RATE_LIMIT_ACTIVO", "True").lower() == "true"
RATE_LIMIT_CHAT = os.getenv("RATE_LIMIT_CHAT", "20/60")
RATE_LIMIT_CHAT_HORA = os.getenv("RATE_LIMIT_CHAT_HORA", "240/3600")
RATE_LIMIT_FEEDBACK = os.getenv("RATE_LIMIT_FEEDBACK", "30/60")
# /propuestas/ no pide login: se limita por IP.
RATE_LIMIT_PROPUESTAS = os.getenv("RATE_LIMIT_PROPUESTAS", "5/3600")

# Deteccion de intentos de secuestro del modelo (chat/antiprompt.py).
ANTIPROMPT_ACTIVO = os.getenv("ANTIPROMPT_ACTIVO", "True").lower() == "true"
ANTIPROMPT_UMBRAL = int(os.getenv("ANTIPROMPT_UMBRAL", "1"))

# Anti-abuso del modelo.
ASISTENTE_MAX_CARACTERES = int(os.getenv("ASISTENTE_MAX_CARACTERES", "2000"))
ASISTENTE_MAX_PEDIDOS_PASO = int(os.getenv("ASISTENTE_MAX_PEDIDOS_PASO", "5"))
# Consultas resueltas por el modelo, por usuario y por dia. 0 = sin limite.
LLM_PRESUPUESTO_DIARIO = int(os.getenv("LLM_PRESUPUESTO_DIARIO", "300"))

if "test" in sys.argv:
    AUTORIZACION_ACTIVA = False
    RATE_LIMIT_ACTIVO = False
    ANTIPROMPT_ACTIVO = False
    LLM_PRESUPUESTO_DIARIO = 0
    # Ningun test debe salir a la API de Drive. Las clases que prueban la
    # sincronizacion la fuerzan con override_settings y mockean el cliente.
    DOCUMENTOS_FUENTE = "local"
