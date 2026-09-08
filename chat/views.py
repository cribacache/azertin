import json
import logging
from datetime import date

from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST

from . import asistente, buk, documentos, respuestas
from .models import contar, registrar

logger = logging.getLogger(__name__)


def responder_con_modelo(mensaje, historial=None, alias=None):
    """Le pregunta a Gemini. Es la unica via de respuesta: no hay reglas de
    respaldo detras.

    Devuelve la respuesta armada, `False` si el modelo respondio con la marca
    NO_SE (sabe que no sabe), o `None` si no se pudo ni siquiera consultarlo
    (sin clave, en pausa por fallas recientes, o la llamada broto un error).
    """
    if not asistente.disponible():
        return None
    try:
        texto, meta = asistente.responder(mensaje, date.today(), historial, alias)
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
        # El propio modelo dijo, con la marca NO_SE, que no pudo responder.
        # Se registra como consulta pendiente en vez de mostrar ese texto tal
        # cual. Se devuelve False (no None) para que quien llama distinga esto
        # de "no se pudo consultar": aca no vale la pena reintentar.
        registrar(mensaje, "sin_datos")
        return False

    return {
        "answer": texto,
        "items": [],
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


def responder_sin_datos(mensaje, ya_registrada=False):
    """El modelo respondio, pero dijo que no tiene el dato (marca NO_SE).
    `ya_registrada` evita registrar dos veces la misma pregunta: quien llama
    ya la guardo con el motivo "sin_datos" dentro de responder_con_modelo."""
    if not ya_registrada:
        registrar(mensaje, "sin_intencion")
    return {
        "answer": (
            "Todavía no tengo esa información. Dejé registrada tu consulta para "
            "incorporarla más adelante. Por ahora puedo ayudarte con la nómina y "
            "la disponibilidad del equipo, y con las políticas y procedimientos "
            "internos que estén cargados."
        ),
        "items": [],
        "meta": {"intencion": "sin_datos", "registrada": True, "requests_buk": 0},
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


def chat_page(request):
    return render(request, "chat/index.html")


@require_POST
def api_feedback(request):
    """El boton de pulgar abajo en una respuesta: la marca como no exitosa.

    Es la senal mas valiosa que guarda ConsultaNoResuelta: el modelo contesto
    con total confianza y quien pregunto dice que estaba mal. Eso es lo que
    hay que revisar primero.
    """
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


@require_GET
def api_status(request):
    # Lo llama la pagina al cargar: recargar empieza una conversacion limpia,
    # sin quedar enganchado al ultimo tema consultado.
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


@require_POST
def chat_message(request):
    try:
        body = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "El mensaje no tiene un formato valido"}, status=400)

    mensaje = str(body.get("message", "")).strip()
    if not mensaje:
        return JsonResponse({"error": "Escribe una pregunta"}, status=400)

    hoy = date.today()

    # Misma pregunta el mismo dia: se sirve del cache, sin BUK ni tokens.
    cacheada = respuestas.obtener(mensaje, hoy)
    if cacheada is not None:
        cacheada = dict(cacheada)
        cacheada["meta"] = {**cacheada.get("meta", {}), "desde_cache": True,
                            "requests_buk": 0}
        contar(mensaje, cacheada["meta"].get("intencion"), desde_cache=True)
        return JsonResponse(cacheada)

    historial_modelo = request.session.get("historial_modelo")
    alias_modelo = request.session.get("alias_modelo")

    del_modelo = responder_con_modelo(mensaje, historial_modelo, alias_modelo)
    if del_modelo:
        respuesta = del_modelo
    elif del_modelo is False:
        respuesta = responder_sin_datos(mensaje, ya_registrada=True)
    else:
        respuesta = responder_no_disponible()

    # Memoria de la conversacion: solo se guarda si el modelo respondio.
    # "en sesion" a proposito: se olvida sola al cerrar el navegador, nada
    # se guarda a largo plazo por ahora.
    nuevo_historial = respuesta.pop("_historial", None)
    nuevo_alias = respuesta.pop("_alias", None)
    if nuevo_historial is not None:
        request.session["historial_modelo"] = nuevo_historial
        request.session["alias_modelo"] = nuevo_alias or {}

    respuesta["meta"]["desde_cache"] = False
    respuestas.guardar(mensaje, hoy, respuesta)
    contar(mensaje, respuesta["meta"].get("intencion"))
    return JsonResponse(respuesta)
