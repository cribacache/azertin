"""Lista las preguntas que el asistente no supo responder, mas repetidas primero."""

from django.core.management.base import BaseCommand

from chat.models import ConsultaNoResuelta, Pregunta

MOTIVOS = dict(ConsultaNoResuelta.MOTIVOS)


class Command(BaseCommand):
    help = "Muestra las consultas que el asistente no pudo responder."

    def add_arguments(self, parser):
        parser.add_argument("--todas", action="store_true",
                            help="Incluye las ya marcadas como resueltas.")
        parser.add_argument("--limite", type=int, default=30)
        parser.add_argument("--resolver", type=int, metavar="ID",
                            help="Marca una consulta como resuelta.")
        parser.add_argument("--frecuentes", action="store_true",
                            help="Muestra las preguntas mas hechas, no las fallidas.")

    def handle(self, *args, **opciones):
        if opciones["resolver"]:
            actualizadas = ConsultaNoResuelta.objects.filter(
                pk=opciones["resolver"]
            ).update(resuelta=True)
            if actualizadas:
                self.stdout.write(self.style.SUCCESS(f"Consulta {opciones['resolver']} resuelta."))
            else:
                self.stdout.write(self.style.ERROR("No existe esa consulta."))
            return

        if opciones["frecuentes"]:
            return self._frecuentes(opciones["limite"])

        filas = ConsultaNoResuelta.objects.all()
        if not opciones["todas"]:
            filas = filas.filter(resuelta=False)
        filas = filas[: opciones["limite"]]

        if not filas:
            self.stdout.write("No hay consultas pendientes.")
            return

        total = sum(f.veces for f in filas)
        self.stdout.write(f"{len(filas)} consultas distintas, {total} preguntas en total.\n")
        for f in filas:
            marca = " (resuelta)" if f.resuelta else ""
            self.stdout.write(
                f"  [{f.pk:4}] x{f.veces:<3} {f.mensaje[:64]:66} "
                f"{MOTIVOS.get(f.motivo, f.motivo)}{marca}"
            )
        self.stdout.write("\nPara marcar una como cubierta: manage.py consultas --resolver ID")

    def _frecuentes(self, limite):
        filas = Pregunta.objects.all()[:limite]
        if not filas:
            self.stdout.write("Todavia no hay preguntas registradas.")
            return

        total = sum(f.veces for f in filas)
        cache = sum(f.veces_cache for f in filas)
        modelo = sum(f.veces_modelo for f in filas)
        self.stdout.write(
            f"{len(filas)} preguntas distintas, {total} en total. "
            f"{cache} servidas desde cache, {modelo} resueltas por el modelo.\n"
        )
        for f in filas:
            self.stdout.write(
                f"  x{f.veces:<4} {f.mensaje[:56]:58} {f.ultima_intencion:12} "
                f"cache={f.veces_cache:<4} modelo={f.veces_modelo}"
            )
        if total:
            self.stdout.write(
                f"\n{cache * 100 // total}% de las preguntas no costaron nada."
            )
