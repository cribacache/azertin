"""Baja la carpeta de Google Drive al cache local, ahora mismo.

La app tambien sincroniza sola cada DRIVE_SYNC_TTL cuando llega una consulta,
pero conviene correr esto en el cron (junto a `precalentar` e `indexar`) para
que la primera consulta del dia no espere la descarga.
"""

from django.conf import settings
from django.core.cache import cache
from django.core.management.base import BaseCommand

from chat import drive


class Command(BaseCommand):
    help = "Sincroniza la carpeta de Google Drive con DRIVE_CACHE_DIR."

    def add_arguments(self, parser):
        parser.add_argument("--forzar", action="store_true",
                            help="Rebaja todo, ignorando el manifiesto local.")

    def handle(self, *args, **opciones):
        if settings.DOCUMENTOS_FUENTE != "drive":
            self.stdout.write(self.style.WARNING(
                "DOCUMENTOS_FUENTE no es 'drive': no hay nada que sincronizar."))
            return
        if not drive.configurado():
            self.stdout.write(self.style.ERROR(
                "Falta GOOGLE_DRIVE_FOLDER_ID o GOOGLE_DRIVE_CREDENTIALS "
                "(o el archivo de credenciales no existe)."))
            return

        resumen = drive.sincronizar(settings.DRIVE_CACHE_DIR,
                                    forzar=opciones["forzar"])
        cache.delete("docs:secciones")   # que la proxima consulta relea
        cache.delete("drive:sync:hecho")

        self.stdout.write(
            f"Bajados: {resumen['descargados']} · omitidos: {resumen['omitidos']} · "
            f"borrados: {resumen['borrados']} · sospechosos: {resumen['sospechosos']}")
        for error in resumen["errores"]:
            self.stdout.write(self.style.ERROR(f"  error: {error}"))
        if not resumen["errores"]:
            self.stdout.write(self.style.SUCCESS("Sincronizacion OK."))
