"""Deja el cache listo antes de que llegue la primera pregunta.

La primera consulta del dia paga el directorio completo y las vacaciones. Si
esto corre por cron temprano, ningun usuario espera esos segundos.
"""

from datetime import date, timedelta

from django.core.management.base import BaseCommand

from chat import buk


class Command(BaseCommand):
    help = "Precarga en cache el directorio y las ausencias de los proximos dias."

    def add_arguments(self, parser):
        parser.add_argument("--dias", type=int, default=7,
                            help="Cuantos dias hacia adelante precargar (default 7).")

    def handle(self, *args, **opciones):
        hoy = date.today()
        try:
            directorio, req = buk.directorio(forzar=True)
        except buk.BukError as error:
            self.stderr.write(self.style.ERROR(f"BUK: {error}"))
            return
        self.stdout.write(f"Directorio: {len(directorio)} personas ({req} requests).")

        total = req
        for dias in range(opciones["dias"]):
            dia = hoy + timedelta(days=dias)
            try:
                registros, req = buk.fuera(dia, dia)
            except buk.BukError as error:
                self.stderr.write(self.style.ERROR(f"{dia}: {error}"))
                continue
            total += req
            self.stdout.write(f"  {dia}: {len(registros)} fuera de jornada ({req} requests)")

        # rangos que la gente pide seguido
        lunes = hoy - timedelta(days=hoy.weekday())
        for etiqueta, d1, d2 in (
            ("esta semana", lunes, lunes + timedelta(days=6)),
            ("este mes", hoy.replace(day=1),
             (hoy.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)),
        ):
            try:
                registros, req = buk.fuera(d1, d2)
                total += req
                self.stdout.write(f"  {etiqueta}: {len(registros)} fuera ({req} requests)")
            except buk.BukError as error:
                self.stderr.write(self.style.ERROR(f"{etiqueta}: {error}"))

        self.stdout.write(self.style.SUCCESS(f"\nCache listo. {total} requests a BUK en total."))
