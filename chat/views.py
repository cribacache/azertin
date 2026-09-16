import json
import logging
from datetime import date

from django.conf import settings
from django.core.cache import cache
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from . import antiprompt, asistente, autorizacion, buk, documentos, perfil, ratelimit, respuestas
from .forms import PropuestaForm
from .models import (
    EventoSeguridad, PerfilUsuario, contar, registrar, registrar_actividad, registrar_evento,
)

logger = logging.getLogger(__name__)


def _client_ip(request):
    """IP de quien pide, para el rate limit de /propuestas/ (sin login).

    Se usa REMOTE_ADDR a secas: X-Forwarded-For lo puede falsear el cliente y
    aca no hay un proxy de confianza declarado. Si en produccion se pone uno
    delante, hay que resolver la IP real segun ese proxy.
    """
    return request.META.get("REMOTE_ADDR") or "desconocida"


def _presupuesto_llm_ok(usuario):
    """Descuenta una consulta del cupo diario del usuario y dice si le queda.

    Cupo aproximado (ventana por dia calendario, contador en el cache
    compartido). 0 = sin limite.
    """
    limite = getattr(settings, "LLM_PRESUPUESTO_DIARIO", 0)
    if not limite:
        return True
    llave = f"llm:{usuario.pk}:{date.today().isoformat()}"
    try:
        if cache.add(llave, 1, 60 * 60 * 26):
            return True
        try:
            return cache.incr(llave) <= limite
        except ValueError:
            cache.set(llave, 1, 60 * 60 * 26)
            return True
    except Exception:  # cache caido: no bloquear por eso
        return True


def responder_con_modelo(mensaje, historial=None, alias=None, contexto=None):
    """Le pregunta a Gemini. Es la unica via de respuesta: no hay reglas de
    respaldo detras.

    Devuelve la respuesta armada (haya o no podido resolver la consulta), o
    `None` si no se pudo ni siquiera consultarlo (sin clave, en pausa por
    fallas recientes, o la llamada broto un error).
    """
    if not asistente.disponible():
        return None
    try:
        texto, meta = asistente.responder(mensaje, date.today(), historial, alias, contexto)
    except asistente.SinConfigurar:
        return None
    except Exception as error:  # el modelo no puede tumbar la aplicacion
        # exc_info para que un error de configuracion no se confunda con una
        # caida del proveedor: sin el traceback, ambos se ven igual.
        logger.warning("el asistente fallo: %s: %s", type(error).__name__, error,
                       exc_info=True)
        return None

    if not texto:
        return None

    if meta.get("exitosa") is False:
        return _respuesta_sin_datos(mensaje, texto, meta)

    return {
        "answer": texto,
        "items": [],
        # Lista de salas de una llamada a salas_disponibles en este turno,
        # aparte de lo que el modelo haya redactado en texto: la interfaz la
        # dibuja tachando la ocupada (chat/asistente.py::_ejecutar_pedidos).
        "salas": meta.get("salas") or [],
        "meta": {
            "intencion": "modelo",
            "modelo": asistente.modelo(),
            "pasos": meta.get("pasos"),
            "herramientas": [h["nombre"] for h in meta.get("herramientas", [])],
            "requests_buk": 0,
        },
        # No van al navegador (se sacan antes de responder): es la memoria que
        # chat_message guarda en la sesion para el proximo mensaje.
        "_historial": meta.get("historial"),
        "_alias": meta.get("alias"),
    }


def _respuesta_sin_datos(mensaje, texto_modelo, meta):
    """El modelo dijo, con la marca NO_SE, que no pudo responder.

    Se registra igual para saber que reforzar despues (ConsultaNoResuelta),
    pero eso ya NO se le cuenta a quien pregunta: decirle "dejé registrada tu
    consulta" sonaba a que el sistema archiva el pedido y sigue de largo, en
    vez de tratar de ayudar en el momento. En su lugar se muestra la
    sugerencia que el propio modelo ya redacto (ver la regla NO_SE en
    asistente.INSTRUCCIONES: le pide reformular o intuir a que se referia),
    salvo que esta MISMA pregunta ya se haya repetido demasiadas veces sin
    entenderse -ahi ya no vale la pena seguir adivinando.
    """
    fila = registrar(mensaje, "sin_datos")
    if fila.veces > settings.ASISTENTE_UMBRAL_SIN_ENTENDER:
        respuesta = ("No logro entender esta solicitud. ¿Podrías contármela con "
                    "otras palabras o darme un poco más de contexto?")
    else:
        respuesta = texto_modelo or "Todavía no tengo esa información."
    return {
        "answer": respuesta,
        "items": [],
        "salas": [],
        "meta": {"intencion": "sin_datos", "requests_buk": 0},
        # Se conserva el historial: si la persona responde a la sugerencia
        # (o al pedido de mas contexto), esa respuesta sigue siendo el
        # seguimiento de esta misma conversacion, no un mensaje suelto.
        "_historial": meta.get("historial"),
        "_alias": meta.get("alias"),
    }


def responder_no_disponible():
    """Gemini no esta disponible ahora mismo: sin clave, sin creditos, cuota
    agotada, o el proveedor no responde. No hay router de reglas detras que
    conteste en su lugar, asi que se avisa en vez de quedarse callado o
    inventar una respuesta a medias."""
    info = asistente.estado()
    motivo = info.get("motivo_legible") or "no está disponible en este momento"
    return {
        "answer": f"No puedo responder ahora mismo: {motivo}. Intenta de nuevo más tarde.",
        "items": [],
        "meta": {"intencion": "no_disponible", "motivo": info.get("motivo"),
                 "requests_buk": 0},
    }


@never_cache
def chat_page(request):
    return render(request, "chat/index.html")


@never_cache
def propuestas_nueva(request):
    """Cualquiera puede proponer una idea, sin iniciar sesion.

    A diferencia de ConsultaNoResuelta (que llena el sistema solo) y del
    admin (que necesita ser staff), esta es la puerta de entrada abierta:
    cualquier persona de Azerta puede dejar una idea. Quien administra el
    backlog la revisa y le cambia el estado despues, desde /admin/.
    """
    if request.method == "POST":
        ip = _client_ip(request)
        if ratelimit.excedido(f"prop:{ip}", settings.RATE_LIMIT_PROPUESTAS):
            registrar_evento(EventoSeguridad.RATE_LIMIT, None, f"/propuestas/ ip={ip}")
            form = PropuestaForm(request.POST)
            form.add_error(None, "Recibimos varias propuestas desde aquí hace poco. "
                                 "Prueba de nuevo en un rato.")
            return render(request, "chat/propuesta_nueva.html",
                          {"form": form, "enviada": False})

        form = PropuestaForm(request.POST)
        if form.is_valid():
            # Si cayó en el honeypot no se guarda, pero se muestra el mismo
            # "gracias": no le confirmamos al bot que lo detectamos.
            if not form.es_spam():
                form.save()
            return render(request, "chat/propuesta_nueva.html",
                         {"form": PropuestaForm(), "enviada": True})
    else:
        form = PropuestaForm()
    return render(request, "chat/propuesta_nueva.html", {"form": form, "enviada": False})


@never_cache
@require_POST
def api_feedback(request):
    """El boton de pulgar abajo en una respuesta: la marca como no exitosa.

    Es la senal mas valiosa que guarda ConsultaNoResuelta: el modelo contesto
    con total confianza y quien pregunto dice que estaba mal. Eso es lo que
    hay que revisar primero.
    """
    if ratelimit.excedido(f"fb:{request.user.pk}", settings.RATE_LIMIT_FEEDBACK):
        return JsonResponse({"ok": False}, status=429)

    try:
        body = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "El mensaje no tiene un formato valido"}, status=400)

    mensaje = str(body.get("message", "")).strip()
    if not mensaje:
        return JsonResponse({"error": "Falta la pregunta original"}, status=400)

    if not bool(body.get("exitosa", True)):
        registrar(mensaje, "marcada_no_exitosa")

    return JsonResponse({"ok": True})


@never_cache
@require_GET
def api_status(request):
    # Esta vista la llama la pagina en DOS momentos distintos: al cargar (ahi
    # SI hay que empezar una conversacion limpia) y despues de CADA mensaje,
    # para refrescar la lucecita de "modelo disponible" (ver app.js). Borrar
    # el historial tambien en ese segundo caso -como pasaba antes- le hacia
    # perder el hilo a la conversacion en cuanto llegaba la primera respuesta:
    # ninguna conversacion de mas de un mensaje podia mantener contexto. Por
    # eso el reseteo es opcional (?nueva=1), y solo lo pide la carga inicial.
    if request.GET.get("nueva"):
        request.session.pop("historial_modelo", None)
        request.session.pop("alias_modelo", None)

    try:
        personas_map, _ = buk.directorio()
    except buk.BukError as error:
        return JsonResponse({"connected": False, "error": str(error)}, status=502)

    return JsonResponse({
        "connected": True,
        "personas_activas": len(personas_map),
        "campos_expuestos": list(buk.CAMPOS_PUBLICOS),
        "documentos": len({s["origen"] for s in documentos.cargar()}),
        "asistente": asistente.estado(),
    })


@never_cache
@require_POST
def chat_message(request):
    usuario = request.user
    ip = _client_ip(request)

    # 1. Frecuencia: rafaga corta y tope por hora, por usuario.
    if (ratelimit.excedido(f"chat:{usuario.pk}", settings.RATE_LIMIT_CHAT)
            or ratelimit.excedido(f"chat-h:{usuario.pk}", settings.RATE_LIMIT_CHAT_HORA)):
        registrar_evento(EventoSeguridad.RATE_LIMIT, usuario, "POST /api/chat/")
        return JsonResponse(
            {"error": "Estás enviando consultas muy seguido. Espera unos segundos."},
            status=429)

    try:
        body = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "El mensaje no tiene un formato valido"}, status=400)

    mensaje = str(body.get("message", "")).strip()
    if not mensaje:
        return JsonResponse({"error": "Escribe una pregunta"}, status=400)

    # 2. Largo: una consulta enorme solo infla el costo del modelo.
    if len(mensaje) > settings.ASISTENTE_MAX_CARACTERES:
        registrar_evento(EventoSeguridad.ENTRADA_LARGA, usuario, f"{len(mensaje)} caracteres")
        registrar_actividad(usuario, mensaje, "entrada_larga", ip=ip)
        return JsonResponse(
            {"error": f"La consulta es muy larga (máximo "
                      f"{settings.ASISTENTE_MAX_CARACTERES} caracteres)."},
            status=400)

    # 3. Inyeccion de prompt: se corta antes de gastar una llamada a Gemini.
    if antiprompt.es_sospechosa(mensaje):
        registrar_evento(EventoSeguridad.INJECTION, usuario, mensaje[:200])
        registrar_actividad(usuario, mensaje, "bloqueada", ip=ip)
        return JsonResponse({
            "answer": ("No puedo procesar esa consulta. Si es una pregunta real sobre "
                       "Azerta, reformúlala sin instrucciones para el asistente."),
            "items": [],
            "meta": {"intencion": "bloqueada", "requests_buk": 0},
        })

    hoy = date.today()
    ctx = perfil.contexto(usuario)

    # 4. Rol sin acceso: no se consulta ni el cache ni el modelo. Se responde
    # como un mensaje normal del bot (200) para que la interfaz lo muestre tal
    # cual en vez de caer en el error generico.
    if ctx.rol == PerfilUsuario.SIN_ACCESO:
        registrar_evento(EventoSeguridad.AUTZ_DENEGADA, usuario, "rol sin_acceso")
        registrar_actividad(usuario, mensaje, "sin_acceso", ip=ip)
        return JsonResponse({
            "answer": autorizacion.MSG_SIN_ACCESO,
            "items": [], "meta": {"intencion": "sin_acceso", "requests_buk": 0},
        })

    ambito = perfil.ambito_cache(ctx)
    historial_modelo = request.session.get("historial_modelo")
    alias_modelo = request.session.get("alias_modelo")
    # El cache es por texto del mensaje: "sí, confírmalo" o "el título es X"
    # significan cosas completamente distintas segun la conversacion, asi que
    # solo se puede servir/guardar del cache el PRIMER mensaje de una
    # conversacion (sin historial en la sesion), nunca un seguimiento. Sin
    # esto, un seguimiento corto y comun (frecuente al reservar una sala)
    # podia responderse con la respuesta cacheada de una conversacion previa
    # de otra persona, sin relacion con lo que se esta hablando ahora.
    es_primer_mensaje = not historial_modelo

    # Misma pregunta el mismo dia y mismo alcance: se sirve del cache.
    cacheada = respuestas.obtener(mensaje, hoy, ambito) if es_primer_mensaje else None
    if cacheada is not None:
        cacheada = dict(cacheada)
        cacheada["meta"] = {**cacheada.get("meta", {}), "desde_cache": True,
                            "requests_buk": 0}
        contar(mensaje, cacheada["meta"].get("intencion"), desde_cache=True)
        registrar_actividad(usuario, mensaje, cacheada["meta"].get("intencion"),
                           desde_cache=True, ip=ip)
        return JsonResponse(cacheada)

    # 5. Cupo diario de consultas al modelo, por usuario.
    if not _presupuesto_llm_ok(usuario):
        registrar_evento(EventoSeguridad.PRESUPUESTO, usuario, "límite diario")
        registrar_actividad(usuario, mensaje, "presupuesto", ip=ip)
        return JsonResponse({
            "answer": "Alcanzaste el máximo de consultas por hoy. Vuelve a intentar mañana.",
            "items": [], "meta": {"intencion": "presupuesto", "requests_buk": 0},
        })

    del_modelo = responder_con_modelo(mensaje, historial_modelo, alias_modelo, ctx)
    respuesta = del_modelo if del_modelo else responder_no_disponible()

    # Memoria de la conversacion: solo se guarda si el modelo respondio.
    # "en sesion" a proposito: se olvida sola al cerrar el navegador, nada
    # se guarda a largo plazo por ahora.
    nuevo_historial = respuesta.pop("_historial", None)
    nuevo_alias = respuesta.pop("_alias", None)
    if nuevo_historial is not None:
        request.session["historial_modelo"] = nuevo_historial
        request.session["alias_modelo"] = nuevo_alias or {}

    respuesta["meta"]["desde_cache"] = False
    if es_primer_mensaje:
        respuestas.guardar(mensaje, hoy, respuesta, ambito)
    contar(mensaje, respuesta["meta"].get("intencion"))
    registrar_actividad(usuario, mensaje, respuesta["meta"].get("intencion"), ip=ip)
    return JsonResponse(respuesta)
