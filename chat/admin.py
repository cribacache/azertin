"""Donde se trabaja el arbol de decisiones ordenadamente.

ConsultaNoResuelta es el backlog: cada fila es una pregunta real que el bot no
supo responder bien (el modelo dijo NO_SE, las reglas no entendieron, o un
usuario marco la respuesta como no exitosa). Se revisa ordenado por `veces`
-la mas repetida primero- y al arreglarla se marca `resuelta` para que no
vuelva a aparecer como pendiente.
"""

from django.contrib import admin

from .models import ConsultaNoResuelta, Pregunta


@admin.register(ConsultaNoResuelta)
class ConsultaNoResueltaAdmin(admin.ModelAdmin):
    list_display = ("mensaje", "motivo", "veces", "resuelta", "primera_vez", "ultima_vez")
    list_filter = ("motivo", "resuelta")
    search_fields = ("mensaje",)
    list_editable = ("resuelta",)
    ordering = ("-veces", "-ultima_vez")
    actions = ["marcar_resueltas", "marcar_pendientes"]

    @admin.action(description="Marcar como resueltas (ya se programo la respuesta)")
    def marcar_resueltas(self, request, queryset):
        queryset.update(resuelta=True)

    @admin.action(description="Volver a marcar como pendientes")
    def marcar_pendientes(self, request, queryset):
        queryset.update(resuelta=False)


@admin.register(Pregunta)
class PreguntaAdmin(admin.ModelAdmin):
    """Solo lectura: todo lo que se pregunta, para ver que conviene cubrir o
    cachear. No se edita nada aca, a diferencia del backlog de arriba."""

    list_display = ("mensaje", "veces", "ultima_intencion", "veces_modelo",
                    "veces_cache", "ultima_vez")
    list_filter = ("ultima_intencion",)
    search_fields = ("mensaje",)
    ordering = ("-veces", "-ultima_vez")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
