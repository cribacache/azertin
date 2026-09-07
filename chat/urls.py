from django.urls import path

from . import views

urlpatterns = [
    path("", views.chat_page, name="chat-page"),
    path("api/status/", views.api_status, name="api-status"),
    path("api/chat/", views.chat_message, name="chat-message"),
    path("api/feedback/", views.api_feedback, name="chat-feedback"),
]
