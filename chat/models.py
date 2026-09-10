from django.conf import settings
from django.db import models


class PerfilUsuario(models.Model):
    """Rol de cada persona que entra al chat, para acotar qué puede preguntar.

    La barrera real vive en `chat/autorizacion.py` (capa de herramientas), no
    en el prompt del modelo. Este modelo solo dice qué rol tiene cada quien;
    lo administra el staff desde `/portal/`.

    - `gerencia`: puede preguntar cualquier cosa sobre cualquier persona.
    - `ejecutivo`: solo información general y datos de personas de su misma
      familia de rol en BUK (Ejecutivos, Consultores, Directores...).
    - `sin_acceso`: no puede usar el chat (baja, contratista, cuenta de
      servicio que igual inició sesión).

    Un `User` sin fila acá se trata como `ROL_DEFECTO` (ejecutivo): una alta
    nueva entra acotada, no bloqueada, y el staff la sube si corresponde.
    """

    GERENCIA = "gerencia"
    EJECUTIVO = "ejecutivo"
    SIN_ACCESO = "sin_acceso"
    ROLES = [
        (GERENCIA, "Gerencia — puede preguntar todo"),
        (EJECUTIVO, "Ejecutivo — acceso acotado a su jerarquía"),
        (SIN_ACCESO, "Sin acceso — no puede usar el chat"),
    ]
    ROL_DEFECTO = EJECUTIVO

    usuario = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="perfil"
    )
    rol = models.CharField(max_length=16, choices=ROLES, default=ROL_DEFECTO)
    # Override manual del cruce con BUK, para cuando el email de la cuenta de
    # Google no coincide con el que BUK tiene registrado.
    buk_employee_id = models.PositiveIntegerField(null=True, blank=True)
    notas = models.CharField(max_length=200, blank=True)
    actualizado_en = models.DateTimeField(auto_now=True)
    actualizado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )

    class Meta:
        verbose_name = "perfil de usuario"
        verbose_name_plural = "perfiles de usuario"
        ordering = ["usuario__email"]

    def __str__(self):
        return f"{self.usuario.email or self.usuario.username}: {self.rol}"


def rol_de(usuario):
    """Rol efectivo de un usuario. Superuser siempre es gerencia (para no
    quedar afuera de su propia herramienta); el resto, lo que diga su perfil,
    o `ROL_DEFECTO` si todavía no tiene."""
    if usuario is None or not usuario.is_authenticated:
        return PerfilUsuario.SIN_ACCESO
    if usuario.is_superuser:
        return PerfilUsuario.GERENCIA
    perfil = PerfilUsuario.objects.filter(usuario=usuario).first()
    return perfil.rol if perfil else PerfilUsuario.ROL_DEFECTO


class EventoSeguridad(models.Model):
    """Registro de lo que el sistema bloqueó o limitó.

    Alimenta la pestaña "Eventos" del portal: sirve para ver si alguien está
    tanteando el chat (inyecciones), si un rol quedó demasiado apretado
    (muchas denegaciones legítimas) o si hay que subir un límite.
    """

    INJECTION = "injection"
    AUTZ_DENEGADA = "autz_denegada"
    RATE_LIMIT = "rate_limit"
    PRESUPUESTO = "presupuesto"
    ENTRADA_LARGA = "entrada_larga"
    TIPOS = [
        (INJECTION, "Posible inyección de prompt"),
        (AUTZ_DENEGADA, "Consulta fuera de alcance del rol"),
        (RATE_LIMIT, "Límite de frecuencia alcanzado"),
        (PRESUPUESTO, "Límite diario de consultas alcanzado"),
        (ENTRADA_LARGA, "Consulta demasiado larga"),
    ]

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    # Copia del identificador aunque después se borre el User.
    email = models.CharField(max_length=254, blank=True)
    tipo = models.CharField(max_length=24, choices=TIPOS, db_index=True)
    detalle = models.CharField(max_length=300, blank=True)
    creado_en = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "evento de seguridad"
        verbose_name_plural = "eventos de seguridad"
        ordering = ["-creado_en"]

    def __str__(self):
        return f"{self.tipo} · {self.email or '?'} · {self.creado_en:%Y-%m-%d %H:%M}"


def registrar_evento(tipo, usuario=None, detalle=""):
    """Anota un evento de seguridad. Nunca interrumpe la petición si falla."""
    try:
        email = ""
        if usuario is not None and getattr(usuario, "is_authenticated", False):
            email = (usuario.email or usuario.get_username() or "")[:254]
        EventoSeguridad.objects.create(
            usuario=usuario if (usuario and usuario.is_authenticated) else None,
            email=email,
            tipo=tipo,
            detalle=str(detalle or "")[:300],
        )
    except Exception:  # el registro es secundario; la respuesta manda
        pass


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
        # El modelo dijo que si sabia y el usuario dice que no: es la senal mas
        # valiosa de las cuatro, porque es una respuesta CONFIADA y equivocada,
        # no una que ya se supiera pendiente.
        ("marcada_no_exitosa", "El usuario marco la respuesta como no exitosa"),
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


class Propuesta(models.Model):
    """Backlog de ideas a futuro: preguntas que Iris todavia no cubre y
    mejoras al proyecto en general.

    Distinta de `ConsultaNoResuelta`: esa la llena el sistema solo, con
    preguntas reales que alguien le hizo al bot. Esta la llena a mano quien
    administra el proyecto, con ideas propias o pedidos que no pasaron por
    el chat (una mejora tecnica, una integracion nueva, etc).
    """

    CATEGORIAS = [
        ("pregunta", "Pregunta que Iris debería poder responder"),
        ("mejora", "Mejora o funcionalidad nueva"),
        ("otro", "Otra idea"),
    ]

    ESTADOS = [
        ("pendiente", "Pendiente"),
        ("en_progreso", "En progreso"),
        ("hecha", "Hecha"),
        ("descartada", "Descartada"),
    ]

    titulo = models.CharField(max_length=200)
    descripcion = models.TextField(blank=True)
    categoria = models.CharField(max_length=16, choices=CATEGORIAS, default="mejora")
    estado = models.CharField(max_length=16, choices=ESTADOS, default="pendiente")
    creada_en = models.DateTimeField(auto_now_add=True)
    actualizada_en = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "propuesta"
        verbose_name_plural = "propuestas"
        ordering = ["-creada_en"]

    def __str__(self):
        return self.titulo


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
