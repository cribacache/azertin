"""Revisa si las consultas que el bot no supo responder ya tienen respuesta.

Es la parte automatizable del ciclo: cuando se agrega un documento, este comando
detecta cuales de las preguntas pendientes quedaron cubiertas y las cierra. Lo
que NO puede hacer es inventar la informacion que falta: si nadie escribio la
respuesta en ningun documento, la pregunta sigue abierta y aparece en el
listado, que es justamente la lista de que documentar.
"""

from django.core.cache import cache
from django.core.management.base import BaseCommand

from chat import documentos, embeddings
from chat.models import ConsultaNoResuelta


class Command(BaseCommand):
    help = "Cierra las consultas pendientes que los documentos actuales ya responden."

    def add_arguments(self, parser):
        parser.add_argument("--aplicar", action="store_true",
                            help="Marca como resueltas. Sin esto solo muestra.")
        parser.add_argument("--stub", metavar="ARCHIVO",
                            help="Escribe una plantilla con las preguntas que "
                                 "siguen sin respuesta, lista para completar.")

    def handle(self, *args, **opciones):
        cache.clear()
        secciones = documentos.cargar(forzar=True)
        if secciones and embeddings.disponible():
            embeddings.indexar(secciones)

        pendientes = list(ConsultaNoResuelta.objects.filter(resuelta=False))
        if not pendientes:
            self.stdout.write("No hay consultas pendientes.")
            return

        # Se prueba el sistema COMPLETO: cada pregunta pasa por Gemini con las
        # herramientas de verdad, asi que esto gasta cuota. "Cubierta" es
        # solo la que el modelo pudo responder (intencion "modelo"): si no
        # hay clave, si esta en pausa por fallas, o si el modelo dijo NO_SE,
        # sigue sin responderse igual que antes.
        from chat.views import responder_con_modelo

        cubiertas, sin_cubrir = [], []
        for fila in pendientes:
            try:
                respuesta = responder_con_modelo(fila.mensaje)
            except Exception:
                respuesta = None
            intencion = (respuesta or {}).get("meta", {}).get("intencion", "")
            if intencion == "modelo":
                cubiertas.append((fila, intencion,
                                  (respuesta.get("answer") or "")[:60]))
            else:
                sin_cubrir.append((fila, "", ""))

        if cubiertas:
            self.stdout.write(self.style.SUCCESS(
                f"\n{len(cubiertas)} consultas que los documentos YA responden:"))
            for fila, intencion, muestra in cubiertas:
                self.stdout.write(
                    f"  x{fila.veces:<3} {fila.mensaje[:46]:48} [{intencion}] {muestra}")

        if sin_cubrir:
            self.stdout.write(self.style.WARNING(
                f"\n{len(sin_cubrir)} siguen sin respuesta (esto es lo que falta documentar):"))
            for fila, _, _ in sorted(sin_cubrir, key=lambda p: -p[0].veces):
                self.stdout.write(f"  x{fila.veces:<3} {fila.mensaje[:70]}")

        if opciones["aplicar"] and cubiertas:
            ConsultaNoResuelta.objects.filter(
                pk__in=[f.pk for f, _, _ in cubiertas]
            ).update(resuelta=True)
            self.stdout.write(self.style.SUCCESS(
                f"\n{len(cubiertas)} marcadas como resueltas."))
        elif cubiertas:
            self.stdout.write("\nPara cerrarlas: manage.py reevaluar --aplicar")

        if opciones["stub"] and sin_cubrir:
            self._escribir_stub(opciones["stub"], sin_cubrir)

    def _escribir_stub(self, ruta, sin_cubrir):
        """Deja el archivo listo para que alguien escriba las respuestas.

        Es hasta donde llega la automatizacion honesta: el sistema arma el
        formulario, la respuesta la pone una persona que sepa.
        """
        lineas = [
            "# Preguntas frecuentes",
            "",
            "Generado por `manage.py reevaluar --stub`. Escribe la respuesta bajo",
            "cada título y guarda el archivo en `datos/`: entra a la búsqueda de",
            "inmediato, sin reiniciar.",
            "",
        ]
        for fila, _, _ in sorted(sin_cubrir, key=lambda p: -p[0].veces):
            lineas += [f"## {fila.mensaje.strip().rstrip('?')}",
                       "",
                       f"<!-- preguntada {fila.veces} vez/veces -->",
                       "TODO: escribir la respuesta.",
                       ""]
        with open(ruta, "w", encoding="utf-8") as f:
            f.write("\n".join(lineas))
        self.stdout.write(self.style.SUCCESS(f"\nPlantilla escrita en {ruta}"))
