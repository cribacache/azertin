from django.db import models


class ConsultaNoResuelta(models.Model):
    """Pregunta que el asistente no supo responder.

    Sirve para decidir que capacidad agregar despues: las que mas se repiten son
    las que conviene cubrir primero.
    """

    MOTIVOS = [
        ("sin_intencion", "No se entendio la pregunta"),
        ("sin_datos", "Se entendio, pero no hay datos"),
        ("persona_desconocida", "Se nombro a alguien que no esta en la nomina"),
        ("persona_ambigua", "El nombre coincide con varias personas"),
    ]

    mensaje = models.TextField()
    mensaje_normalizado = models.CharField(max_length=500, db_index=True)
    motivo = models.CharField(max_length=32, choices=MOTIVOS, default="sin_intencion")
    veces = models.PositiveIntegerField(default=1)
    primera_vez = models.DateTimeField(auto_now_add=True)
    ultima_vez = models.DateTimeField(auto_now=True)
    resuelta = models.BooleanField(default=False)

    class Meta:
        verbose_name = "consulta no resuelta"
        verbose_name_plural = "consultas no resueltas"
        ordering = ["-veces", "-ultima_vez"]

    def __str__(self):
        return f"{self.mensaje[:60]} (x{self.veces})"


def registrar(mensaje, motivo="sin_intencion"):
    """Guarda la consulta, agrupando las repeticiones en una sola fila."""
    from chat.intents import normalizar

    clave = normalizar(mensaje)[:500]
    fila, creada = ConsultaNoResuelta.objects.get_or_create(
        mensaje_normalizado=clave,
        defaults={"mensaje": mensaje, "motivo": motivo},
    )
    if not creada:
        ConsultaNoResuelta.objects.filter(pk=fila.pk).update(
            veces=models.F("veces") + 1, motivo=motivo, resuelta=False
        )
    return fila


class Pregunta(models.Model):
    """Toda pregunta que llega, con cuantas veces y como se resolvio.

    Sirve para dos cosas: saber que se pregunta mas (y por lo tanto que conviene
    tener cacheado o cubierto por reglas) y cuanto esta costando el modelo.
    """

    mensaje = models.TextField()
    mensaje_normalizado = models.CharField(max_length=500, unique=True)
    veces = models.PositiveIntegerField(default=1)
    ultima_intencion = models.CharField(max_length=32, blank=True)
    veces_modelo = models.PositiveIntegerField(default=0)
    veces_cache = models.PositiveIntegerField(default=0)
    primera_vez = models.DateTimeField(auto_now_add=True)
    ultima_vez = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "preguntas"
        ordering = ["-veces", "-ultima_vez"]

    def __str__(self):
        return f"{self.mensaje[:60]} (x{self.veces})"


def contar(mensaje, intencion, desde_cache=False):
    """Registra la pregunta. Nunca interrumpe la respuesta si falla."""
    from chat.intents import normalizar

    clave = normalizar(mensaje)[:500]
    try:
        fila, creada = Pregunta.objects.get_or_create(
            mensaje_normalizado=clave,
            defaults={"mensaje": mensaje, "ultima_intencion": intencion or ""},
        )
        if not creada:
            Pregunta.objects.filter(pk=fila.pk).update(
                veces=models.F("veces") + 1,
                ultima_intencion=intencion or "",
                veces_modelo=models.F("veces_modelo") + (1 if intencion == "modelo" else 0),
                veces_cache=models.F("veces_cache") + (1 if desde_cache else 0),
            )
        elif intencion == "modelo":
            Pregunta.objects.filter(pk=fila.pk).update(veces_modelo=1)
    except Exception:  # el registro es secundario; la respuesta manda
        pass
