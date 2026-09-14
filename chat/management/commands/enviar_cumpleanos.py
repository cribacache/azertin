"""Manda la tarjeta de cumpleanos de cada persona que cumple hoy.

Pensado para correr una vez al dia via Cloud Scheduler + Cloud Run Job
(mismo patron que `sincronizar_drive`/`indexar`), no a mano.
"""

from datetime import date

from django.core.management.base import BaseCommand

from chat import buk, cumpleanos_foto


class Command(BaseCommand):
    help = "Manda por Gmail la tarjeta de cumpleaños de quienes cumplen hoy."

    def handle(self, *args, **opciones):
        personas, _ = buk.cumpleanos(date.today())
        if not personas:
            self.stdout.write("Nadie cumple hoy.")
            return

        for persona in personas:
            try:
                cumpleanos_foto.enviar_tarjeta(persona)
            except cumpleanos_foto.SinFoto as error:
                self.stdout.write(self.style.WARNING(f"{persona['nombre']}: {error}"))
            except Exception as error:  # noqa: BLE001 - se reporta, no debe tumbar el resto
                self.stdout.write(self.style.ERROR(f"{persona['nombre']}: {error}"))
            else:
                self.stdout.write(self.style.SUCCESS(f"{persona['nombre']}: tarjeta enviada"))
