"""Filtros para renderizar un campo de formulario con atributos extra.

Se usa en templates/portal/turnos.html: cada fila de la tabla es su propio
<form> (por el atributo HTML `form=`, no anidando un <form> dentro de la
fila -invalido en una tabla y era la causa de que la tabla quedara
desalineada de su encabezado). Los <select>/<textarea> viven en la celda,
pero apuntan al <form> de su fila por id.
"""

from django import template

register = template.Library()


@register.filter
def con_formulario(campo, form_id):
    """Renderiza `campo` (un BoundField) agregandole el atributo form=form_id."""
    return campo.as_widget(attrs={"form": form_id})
