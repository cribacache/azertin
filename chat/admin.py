"""Donde se trabaja el arbol de decisiones ordenadamente.

ConsultaNoResuelta es el backlog automatico: cada fila es una pregunta real
que el bot no supo responder bien (el modelo dijo NO_SE, o un usuario marco
la respuesta como no exitosa). Se revisa ordenado por `veces` -la mas
repetida primero- y al arreglarla se marca `resuelta` para que no vuelva a
aparecer como pendiente.

Propuesta es el backlog manual: ideas propias de mejoras o preguntas nuevas
a cubrir, que no vinieron de una pregunta real en el chat.
"""

from django.contrib import admin, messages

from .models import (ConsultaNoResuelta, EventoSeguridad, PerfilUsuario,
                     Pregunta, Propuesta)


@admin.register(PerfilUsuario)
class PerfilUsuarioAdmin(admin.ModelAdmin):
    """El trabajo del dia a dia va en /portal/; esto es el respaldo."""

    list_display = ("usuario", "rol", "buk_employee_id", "actualizado_en",
                    "actualizado_por")
    list_filter = ("rol",)
    search_fields = ("usuario__email", "usuario__username", "notas")
    list_editable = ("rol",)
    ordering = ("usuario__email",)

    def save_model(self, request, obj, form, change):
        # Asignar "gerencia" (acceso total) queda reservado a superusuarios,
        # igual que en /portal/.
        if obj.rol == PerfilUsuario.GERENCIA and not request.user.is_superuser:
            messages.error(request,
                           "Solo un superusuario puede asignar el rol gerencia.")
            if change:
                obj.rol = type(obj).objects.get(pk=obj.pk).rol
            else:
                obj.rol = PerfilUsuario.EJECUTIVO
        obj.actualizado_por = request.user
        super().save_model(request, obj, form, change)


@admin.register(EventoSeguridad)
class EventoSeguridadAdmin(admin.ModelAdmin):
    list_display = ("creado_en", "tipo", "email", "detalle")
    list_filter = ("tipo", "creado_en")
    search_fields = ("email", "detalle")
    ordering = ("-creado_en",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


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


@admin.register(Propuesta)
class PropuestaAdmin(admin.ModelAdmin):
    list_display = ("titulo", "categoria", "estado", "creada_en", "actualizada_en")
    list_filter = ("categoria", "estado")
    search_fields = ("titulo", "descripcion")
    list_editable = ("estado",)
    ordering = ("-creada_en",)


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
