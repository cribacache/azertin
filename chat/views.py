import json
import logging
from collections import Counter
from datetime import date

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST

from . import asistente, buk, documentos, intents, personas, respuestas
from .models import contar, registrar

logger = logging.getLogger(__name__)

MESES_ES = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
            "agosto", "septiembre", "octubre", "noviembre", "diciembre")


def _dias(desde, hasta):
    try:
        d1 = date.fromisoformat(desde)
        d2 = date.fromisoformat(hasta) if hasta else d1
        return (d2 - d1).days + 1
    except (TypeError, ValueError):
        return None


def _fecha_larga(iso):
    try:
        d = date.fromisoformat(iso)
    except (TypeError, ValueError):
        return iso or "?"
    return f"{d.day} de {MESES_ES[d.month - 1]}"


def armar_item(registro, personas_map):
    """Convierte un registro normalizado en un item publico (sin PII)."""
    persona = personas_map.get(registro["employee_id"]) or {}
    cfg = buk.CATEGORIAS.get(registro["categoria"], {})
    return {
        "id": registro["employee_id"],
        "nombre": persona.get("nombre") or f"Empleado #{registro['employee_id']}",
        "cargo": persona.get("cargo") or "",
        "tipo": cfg.get("etiqueta", registro["categoria"]),
        "detalle": registro.get("detalle") or "",
        "desde": registro["start_date"],
        "hasta": registro["end_date"],
        "dias": _dias(registro["start_date"], registro["end_date"]),
        "estado": "aprobada" if registro.get("status") == "approved" else "pendiente",
        "media_jornada": bool(registro.get("media_jornada")),
    }


def responder_persona(plan, ids, personas_map):
    """Respuesta en texto sobre una persona concreta, no un listado."""
    registros, req = buk.fuera(plan["desde"], plan["hasta"], plan.get("categoria"),
                               plan.get("subtipo"))
    pid = next(iter(ids))
    persona = personas_map.get(pid, {})
    nombre = persona.get("nombre", f"Empleado #{pid}")
    suyos = sorted((r for r in registros if r["employee_id"] == pid),
                   key=lambda r: r["start_date"] or "")
    etiqueta = plan["etiqueta"]

    if not suyos:
        # Responder "no tiene ausencias" es una respuesta correcta, no un fallo:
        # no se registra como consulta pendiente.
        texto = f"{nombre} está en su jornada {etiqueta}: no registra ausencias en BUK."
    else:
        partes = []
        for r in suyos:
            cfg = buk.CATEGORIAS.get(r["categoria"], {})
            tipo = cfg.get("etiqueta", r["categoria"])
            desde, hasta = r["start_date"], r["end_date"]
            if hasta and hasta != desde:
                partes.append(f"{tipo} del {_fecha_larga(desde)} al {_fecha_larga(hasta)}")
            else:
                partes.append(f"{tipo} el {_fecha_larga(desde)}")
        texto = f"{nombre} tiene {'; '.join(partes)}."
        if persona.get("cargo"):
            texto = f"{texto[:-1]} ({persona['cargo']})."

    return {
        "answer": texto,
        "items": [armar_item(r, personas_map) for r in suyos],
        "meta": {"intencion": "persona", "persona": nombre, "requests_buk": req},
    }


def responder_ausencias(plan):
    categoria, etiqueta = plan.get("categoria"), plan["etiqueta"]
    subtipo = plan.get("subtipo")
    personas_map, req_dir = buk.directorio()

    # Si la pregunta nombra a alguien, se responde por esa persona.
    ids, tokens = personas.buscar(plan["mensaje"], personas_map)
    if len(ids) == 1:
        respuesta = responder_persona(plan, ids, personas_map)
        respuesta["meta"]["requests_buk"] += req_dir
        return respuesta
    if len(ids) > 1:
        registrar(plan["mensaje"], "persona_ambigua")
        nombres = sorted(personas_map[i]["nombre"] for i in ids)[:6]
        return {
            "answer": ("Encontré varias personas con ese nombre: "
                       + ", ".join(nombres)
                       + ". ¿Por cuál de ellas te refieres? Dime el nombre y el apellido."),
            "items": [],
            "meta": {"intencion": "persona_ambigua", "requests_buk": req_dir},
        }

    registros, req = buk.fuera(plan["desde"], plan["hasta"], categoria, subtipo)
    items = sorted((armar_item(r, personas_map) for r in registros),
                   key=lambda i: (i["desde"] or "", i["nombre"]))

    if categoria:
        nombre = buk.CATEGORIAS[categoria]["etiqueta"]
        if subtipo:
            nombre = buk.TIPOS_VACACION_PLURAL.get(subtipo, nombre)
        if not items:
            texto = f"No hay nadie con {nombre} {etiqueta}."
        elif len(items) == 1:
            i = items[0]
            texto = (f"Hay 1 persona con {nombre} {etiqueta}: {i['nombre']}, "
                     f"del {i['desde']} al {i['hasta']}.")
        else:
            texto = f"Hay {len(items)} personas con {nombre} {etiqueta}. Te las dejo abajo."
    elif not items:
        texto = f"El equipo está completo {etiqueta}: nadie registra ausencias."
    else:
        resumen = Counter(i["tipo"] for i in items)
        detalle = ", ".join(f"{n} con {t}" for t, n in resumen.most_common())
        plural = "persona" if len(items) == 1 else "personas"
        texto = f"Hay {len(items)} {plural} fuera de su jornada {etiqueta}: {detalle}."

    return {
        "answer": texto,
        "items": items,
        "meta": {
            "intencion": "ausencias",
            "categoria": categoria or "todas",
            "subtipo": subtipo,
            "rango": f"{plan['desde']} a {plan['hasta']}",
            "requests_buk": req + req_dir,
        },
    }


def responder_dotacion():
    personas_map, req = buk.directorio()
    return {
        "answer": f"Actualmente hay {len(personas_map)} personas activas en la nómina.",
        "items": [],
        "meta": {"intencion": "dotacion", "requests_buk": req},
    }


def responder_documento(seccion):
    return {
        "answer": seccion["cuerpo"],
        "items": [],
        "meta": {
            "intencion": "documento",
            "fuente": seccion["origen"],
            "seccion": seccion["titulo"],
            "requests_buk": 0,
        },
    }


def responder_con_modelo(mensaje):
    """Respaldo con modelo de lenguaje. None si no hay clave o si no resolvio."""
    if not asistente.disponible():
        return None
    try:
        texto, meta = asistente.responder(mensaje, date.today())
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
    return {
        "answer": texto,
        "items": [],
        "meta": {
            "intencion": "modelo",
            "modelo": settings.OPENAI_MODEL,
            "pasos": meta.get("pasos"),
            "herramientas": [h["nombre"] for h in meta.get("herramientas", [])],
            "requests_buk": 0,
        },
    }


def responder_sin_datos(mensaje):
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


def chat_page(request):
    return render(request, "chat/index.html")


@require_GET
def api_status(request):
    try:
        personas_map, _ = buk.directorio()
    except buk.BukError as error:
        return JsonResponse({"connected": False, "error": str(error)}, status=502)

    return JsonResponse({
        "connected": True,
        "personas_activas": len(personas_map),
        "campos_expuestos": list(buk.CAMPOS_PUBLICOS),
        "documentos": len({s["origen"] for s in documentos.cargar()}),
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

    try:
        respuesta = _resolver(mensaje, hoy)
    except buk.BukError as error:
        return JsonResponse({"error": str(error)}, status=502)

    respuesta["meta"]["desde_cache"] = False
    respuestas.guardar(mensaje, hoy, respuesta)
    contar(mensaje, respuesta["meta"].get("intencion"))
    return JsonResponse(respuesta)


def _resolver(mensaje, hoy):
    """Decide quien responde. El orden va de lo barato a lo caro."""
    # Modo "todo por el modelo": mejor criterio, mas costo por pregunta.
    if settings.ASISTENTE_SIEMPRE and asistente.disponible():
        del_modelo = responder_con_modelo(mensaje)
        if del_modelo:
            return del_modelo

    # Comparaciones, agregaciones o filtros que las reglas no saben resolver.
    # Antes contestaban una lista equivocada; ahora las toma el modelo, y si no
    # hay modelo se dice que no se puede en vez de inventar.
    if intents.es_compleja(mensaje):
        # "cuantos dias de vacaciones me corresponden" es compleja para BUK pero
        # la responde la politica. Solo se busca en documentos si ademas es una
        # pregunta de procedimiento: "compara agosto con septiembre" es una
        # consulta de datos y no debe terminar citando el reglamento.
        if intents.es_procedimiento(mensaje):
            seccion = documentos.responder(mensaje)
            if seccion:
                return responder_documento(seccion)
        del_modelo = responder_con_modelo(mensaje)
        if del_modelo:
            return del_modelo
        return responder_sin_datos(mensaje)

    plan = intents.interpretar(mensaje)
    plan["mensaje"] = mensaje

    # "como pido vacaciones" menciona vacaciones pero pregunta por el
    # procedimiento: los documentos responden antes que BUK.
    if intents.es_procedimiento(mensaje):
        seccion = documentos.responder(mensaje)
        if seccion:
            return responder_documento(seccion)

    if plan["intencion"] == "ausencias":
        return responder_ausencias(plan)
    if plan["intencion"] == "dotacion":
        return responder_dotacion()

    # No se reconocio la intencion, pero puede nombrar a alguien: "y Duk?",
    # "cuando vuelve Javiera?". Se responde por esa persona, para hoy.
    try:
        personas_map, req_dir = buk.directorio()
        ids, _ = personas.buscar(mensaje, personas_map)
        if len(ids) == 1:
            respuesta = responder_persona(
                {"desde": hoy, "hasta": hoy, "etiqueta": "hoy", "mensaje": mensaje},
                ids, personas_map,
            )
            respuesta["meta"]["requests_buk"] += req_dir
            return respuesta
    except buk.BukError:
        pass  # sin BUK igual se puede responder desde los documentos

    seccion = documentos.responder(mensaje)
    if seccion:
        return responder_documento(seccion)

    del_modelo = responder_con_modelo(mensaje)
    if del_modelo:
        return del_modelo

    return responder_sin_datos(mensaje)
