"""Prepara el indice semantico de los documentos.

Solo calcula los fragmentos nuevos: agregar un PDF cuesta unicamente sus propias
secciones, no todo el corpus.
"""

from django.core.management.base import BaseCommand
from django.core.cache import cache

from chat import documentos, embeddings


class Command(BaseCommand):
    help = "Calcula los embeddings de los documentos de datos/."

    def add_arguments(self, parser):
        parser.add_argument("--forzar", action="store_true",
                            help="Recalcula todo, ignorando el indice guardado.")

    def handle(self, *args, **opciones):
        cache.clear()
        secciones = documentos.cargar(forzar=True)
        if not secciones:
            self.stdout.write("No hay documentos en datos/.")
            return

        archivos = sorted({s["origen"] for s in secciones})
        self.stdout.write(f"{len(secciones)} secciones en {len(archivos)} archivos:")
        for archivo in archivos:
            cuantas = sum(1 for s in secciones if s["origen"] == archivo)
            self.stdout.write(f"  {archivo}: {cuantas} secciones")

        if not embeddings.disponible():
            self.stdout.write(self.style.WARNING(
                "\nSin GEMINI_API_KEY o con EMBEDDINGS_ACTIVOS=False: "
                "queda solo la busqueda por palabras."))
            return

        self.stdout.write("\nCalculando embeddings...")
        def avance(hechos, total):
            self.stdout.write(f"  {hechos}/{total}", ending="\r")
            self.stdout.flush()

        pedidos, fallidos = embeddings.indexar(
            secciones, forzar=opciones["forzar"], progreso=avance)

        if pedidos:
            self.stdout.write(self.style.SUCCESS(
                f"{pedidos} fragmentos nuevos indexados."))
        if fallidos:
            # Distinto de "no habia nada que hacer": estos si quedaron sin
            # vector y hay que volver a correr el comando.
            self.stdout.write(self.style.ERROR(
                f"{fallidos} fragmentos NO se pudieron indexar (cuota agotada). "
                "Vuelve a correr el comando en unos minutos: lo ya calculado se "
                "conserva y solo se piden los que faltan."))
        elif not pedidos:
            self.stdout.write("Todo estaba al dia, no se pidio nada.")
