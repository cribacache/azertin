from django import forms

from .models import Propuesta


class PropuestaForm(forms.ModelForm):
    """Lo que llena cualquiera desde `/propuestas/`, sin iniciar sesion.

    Solo los tres campos que tiene sentido que decida quien propone: el
    estado lo cambia despues quien administra el backlog, desde el admin.
    """

    # Trampa para bots: un humano no la ve (la oculta el CSS, clase
    # .campo-trampa en app.css) y tiene autocomplete off. Si viene con algo,
    # la vista descarta el envio.
    web = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            "autocomplete": "off", "tabindex": "-1",
            "class": "campo-trampa", "aria-hidden": "true",
        }),
        label="",
    )

    def es_spam(self):
        return bool(self.data.get("web"))

    class Meta:
        model = Propuesta
        fields = ["categoria", "titulo", "descripcion"]
        labels = {
            "categoria": "¿Qué tipo de idea es?",
            "titulo": "En una frase, ¿cuál es la idea?",
            "descripcion": "Cuéntanos más (opcional)",
        }
        widgets = {
            "categoria": forms.RadioSelect,
            "titulo": forms.TextInput(attrs={
                "placeholder": "Ej: que Iris avise cuando alguien renuncia",
                "maxlength": 200,
                "autofocus": True,
            }),
            "descripcion": forms.Textarea(attrs={
                "rows": 4,
                "placeholder": "El contexto que quieras agregar.",
            }),
        }
