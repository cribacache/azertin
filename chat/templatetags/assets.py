"""Versiona los estaticos por fecha de modificacion.

Sin esto el navegador se queda con el CSS o el JS anterior despues de cada
edicion, y el cambio parece no haber ocurrido.
"""

import os

from django.contrib.staticfiles import finders
from django.template import Library
from django.templatetags.static import static

register = Library()


@register.simple_tag
def static_v(ruta):
    url = static(ruta)
    absoluta = finders.find(ruta)
    if absoluta and os.path.exists(absoluta):
        return f"{url}?v={int(os.path.getmtime(absoluta))}"
    return url
