"""Adaptadores de django-allauth: solo Google, solo el dominio de Azerta.

Sin cuenta local con usuario y clave -la unica forma de entrar es con una
cuenta de Google del dominio en GOOGLE_WORKSPACE_DOMAIN (config/settings.py).
"""

from allauth.account.adapter import DefaultAccountAdapter
from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.conf import settings
from django.contrib import messages
from django.shortcuts import redirect


class SoloAzertaAccountAdapter(DefaultAccountAdapter):
    """Cierra el registro con usuario y clave: la unica puerta es Google."""

    def is_open_for_signup(self, request):
        return False


class SoloAzertaSocialAdapter(DefaultSocialAccountAdapter):
    def is_open_for_signup(self, request, sociallogin):
        # El filtro real pasa en pre_social_login; aca no hay que negar por
        # dominio de nuevo, alcanza con dejar seguir el flujo normal.
        return True

    def pre_social_login(self, request, sociallogin):
        """Corta el login si el email de Google no es del dominio de Azerta.

        Se revisa aca y no solo con el parametro `hd` en la pantalla de
        Google: ese parametro es una sugerencia visual que se puede evitar
        eligiendo otra cuenta ya logueada en el navegador. Esta es la
        barrera real, del lado del servidor.
        """
        email = (sociallogin.account.extra_data.get("email") or "").lower()
        dominio = f"@{settings.GOOGLE_WORKSPACE_DOMAIN.lower()}"
        if not email.endswith(dominio):
            messages.error(
                request,
                f"Esta app es solo para cuentas {dominio}. "
                f"Iniciaste sesión con {email or 'una cuenta que no pude leer'}.",
            )
            raise ImmediateHttpResponse(redirect("account_login"))
