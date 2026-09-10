from django.urls import path

from . import portal, views

urlpatterns = [
    path("", views.chat_page, name="chat-page"),
    path("propuestas/", views.propuestas_nueva, name="propuestas-nueva"),
    path("api/status/", views.api_status, name="api-status"),
    path("api/chat/", views.chat_message, name="chat-message"),
    path("api/feedback/", views.api_feedback, name="chat-feedback"),
    # Portal de administracion (solo staff, detras del login de Google).
    path("portal/", portal.usuarios, name="portal-usuarios"),
    path("portal/eventos/", portal.eventos, name="portal-eventos"),
]
