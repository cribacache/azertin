"""Lista los modelos que la clave configurada puede usar.

Los nombres cambian seguido: en vez de fiarse de un default, conviene mirar que
habilita la cuenta y poner ese valor en GEMINI_MODEL.
"""

from django.conf import settings
from django.core.management.base import BaseCommand

from chat import asistente


class Command(BaseCommand):
    help = "Muestra los modelos de Gemini disponibles para la clave configurada."

    def handle(self, *args, **opciones):
        if not asistente.disponible():
            self.stderr.write(self.style.ERROR(
                "No hay clave para Gemini. Configura GEMINI_API_KEY en .env."))
            return

        self.stdout.write(f"Configurado: {asistente.modelo()}\n")

        try:
            cliente = asistente._cliente_gemini()
            nombres = []
            for m in cliente.models.list():
                acciones = getattr(m, "supported_actions", None) or []
                if not acciones or "generateContent" in acciones:
                    nombres.append(m.name.replace("models/", ""))
        except Exception as error:
            self.stderr.write(self.style.ERROR(f"No se pudo consultar: {error}"))
            return

        for nombre in nombres:
            marca = "  <- configurado" if nombre == asistente.modelo() else ""
            self.stdout.write(f"  {nombre}{marca}")

        if asistente.modelo() not in nombres:
            self.stdout.write(self.style.WARNING(
                f"\nOjo: '{asistente.modelo()}' no aparece en la lista. "
                f"Ajusta GEMINI_MODEL en .env."))
        self.stdout.write(f"\n{len(nombres)} modelos disponibles.")
