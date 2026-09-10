from django.conf import settings


def dominio_google(request):
    """Para mostrar que dominio de correo se pide en la pantalla de login."""
    return {"GOOGLE_WORKSPACE_DOMAIN": settings.GOOGLE_WORKSPACE_DOMAIN}
