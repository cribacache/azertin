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

    def save_user(self, request, sociallogin, form=None):
        """Aplica una InvitacionRol pendiente para este correo, si hay una.

        Solo se llama en el ALTA de la cuenta (primer login de esa persona),
        nunca en logins siguientes: es justo cuando el `User` recien se crea
        y `PerfilUsuario` puede referenciarlo. Si nadie dejo una invitacion,
        no hace nada distinto y la persona entra con el rol por defecto,
        como siempre.
        """
        usuario = super().save_user(request, sociallogin, form)
        self._aplicar_invitacion(usuario)
        return usuario

    @staticmethod
    def _aplicar_invitacion(usuario):
        from .models import InvitacionRol, PerfilUsuario

        email = (usuario.email or "").strip().lower()
        if not email:
            return
        invitacion = InvitacionRol.objects.filter(email=email).first()
        if invitacion is None:
            return
        PerfilUsuario.objects.update_or_create(
            usuario=usuario, defaults={"rol": invitacion.rol}
        )
        invitacion.delete()
