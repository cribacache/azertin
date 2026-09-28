from django import forms

from .models import Propuesta, Turno


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


class _TurnoWidgets:
    """Comun a los dos forms de abajo: mismos selects, mismas clases para
    que chat/turnos_portal.js sepa a que fila referirse."""

    departamento = forms.Select(attrs={"class": "campo-departamento"})
    forma_trabajo = forms.Select(attrs={"class": "campo-forma-trabajo"})
    modalidad = forms.Select(attrs={"class": "campo-modalidad"})
    observacion = forms.Textarea(attrs={
        "class": "campo-observacion", "rows": 2,
        "placeholder": "A qué corresponde (solo Acuerdo / Conciliación familiar).",
    })


class TurnoCrearForm(forms.ModelForm):
    """Alta de una asignacion nueva: unica vez que se elige el empleado
    (despues no se puede cambiar, ver TurnoEditarForm). `buk_employee_id` es
    un ChoiceField, no el PositiveIntegerField del modelo: las opciones
    (activos de BUK sin turno asignado todavia) las arma la vista, que es
    quien tiene el directorio a mano.
    """

    buk_employee_id = forms.ChoiceField(label="Empleado")

    def __init__(self, *args, opciones_empleado=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["buk_employee_id"].choices = opciones_empleado

    class Meta:
        model = Turno
        fields = ["buk_employee_id", "departamento", "forma_trabajo", "modalidad", "observacion"]
        widgets = {
            "departamento": _TurnoWidgets.departamento,
            "forma_trabajo": _TurnoWidgets.forma_trabajo,
            "modalidad": _TurnoWidgets.modalidad,
            "observacion": _TurnoWidgets.observacion,
        }


class TurnoEditarForm(forms.ModelForm):
    """Edicion en la fila de la tabla: todo menos el empleado (eso no
    cambia una vez creada la fila; si se equivocaron de persona, se borra y
    se crea de nuevo)."""

    class Meta:
        model = Turno
        fields = ["departamento", "forma_trabajo", "modalidad", "observacion"]
        widgets = {
            "departamento": _TurnoWidgets.departamento,
            "forma_trabajo": _TurnoWidgets.forma_trabajo,
            "modalidad": _TurnoWidgets.modalidad,
            "observacion": _TurnoWidgets.observacion,
        }
