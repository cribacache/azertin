from django.conf import settings


def dominio_google(request):
    """Para mostrar que dominio de correo se pide en la pantalla de login."""
    return {"GOOGLE_WORKSPACE_DOMAIN": settings.GOOGLE_WORKSPACE_DOMAIN}


def turnos_portal(request):
    """Para mostrar u ocultar el boton a /rrhh/turnos/ en la pagina
    principal (templates/chat/index.html): mismo criterio de acceso que
    chat/turnos_portal.py::_solo_turnos, para no ofrecer un link que va a
    dar 403."""
    correo = (getattr(request.user, "email", "") or "").strip().lower()
    return {"PUEDE_VER_TURNOS": correo in settings.TURNOS_PORTAL_USUARIOS}
