"""Middleware propio del proyecto.

- RequiereLoginMiddleware: todo el sitio pide sesion iniciada, salvo
  login/OAuth, estaticos y /admin/ (que tiene su propio login).
- SecurityHeadersMiddleware: cabeceras que Django no pone solo (CSP,
  Permissions-Policy, Cross-Origin-Resource-Policy).
- AdminBruteForceMiddleware: frena el fuerza-bruta contra /admin/login/.
"""

import logging

from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.http import HttpResponse

from . import ratelimit

logger = logging.getLogger(__name__)

# /admin/ tiene su propio login (usuario y clave, para quien administra el
# backlog de preguntas) y no pasa por Google: queda afuera para no forzar un
# login de Google antes de llegar a esa pantalla, que ya estaba protegida.
EXENTAS = ("/accounts/", "/static/", "/admin/")

ADMIN_LOGIN = "/admin/login/"


class RequiereLoginMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated or request.path.startswith(EXENTAS):
            return self.get_response(request)
        return redirect_to_login(request.get_full_path())


# ---------------------------------------------------------------------------
# Cabeceras de seguridad
# ---------------------------------------------------------------------------

# Politica estricta para las paginas propias: sin nada inline (el JS y el CSS
# viven en /static/), fuentes solo de Google Fonts, conexiones solo al propio
# origen, y nada de que la pagina viva dentro de un iframe.
_CSP_APP = (
    "default-src 'self'; "
    "base-uri 'self'; "
    "object-src 'none'; "
    "frame-ancestors 'none'; "
    "form-action 'self'; "
    "img-src 'self' data:; "
    "script-src 'self'; "
    "style-src 'self' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; "
    "connect-src 'self'"
)

# El admin de Django usa estilos y scripts inline; una CSP sin 'unsafe-inline'
# lo rompe. Se le deja una version mas laxa (igual bloquea scripts externos,
# el embebido en iframe y object-src).
_CSP_ADMIN = (
    "default-src 'self'; "
    "base-uri 'self'; "
    "object-src 'none'; "
    "frame-ancestors 'none'; "
    "form-action 'self'; "
    "img-src 'self' data:; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "connect-src 'self'"
)

_PERMISSIONS_POLICY = (
    "camera=(), microphone=(), geolocation=(), payment=(), usb=(), "
    "interest-cohort=()"
)


class SecurityHeadersMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        csp = _CSP_ADMIN if request.path.startswith("/admin/") else _CSP_APP
        # setdefault: si una vista ya puso su propia cabecera, se respeta.
        response.headers.setdefault("Content-Security-Policy", csp)
        response.headers.setdefault("Permissions-Policy", _PERMISSIONS_POLICY)
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        return response


# ---------------------------------------------------------------------------
# Fuerza-bruta contra el login del admin
# ---------------------------------------------------------------------------

class AdminBruteForceMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method == "POST" and request.path == ADMIN_LOGIN:
            ip = request.META.get("REMOTE_ADDR") or "desconocida"
            if ratelimit.excedido(f"adminlogin:{ip}", settings.RATE_LIMIT_ADMIN_LOGIN):
                from .models import EventoSeguridad, registrar_evento

                registrar_evento(EventoSeguridad.RATE_LIMIT, None,
                                 f"/admin/login/ ip={ip}")
                logger.warning("rate limit en /admin/login/ desde %s", ip)
                return HttpResponse(
                    "Demasiados intentos de inicio de sesión. Probá de nuevo en unos minutos.",
                    status=429, content_type="text/plain; charset=utf-8")
        return self.get_response(request)
