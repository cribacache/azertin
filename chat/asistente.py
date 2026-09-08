"""Capa de lenguaje natural sobre las herramientas de `chat/herramientas.py`.

Es la unica via de respuesta: no hay un router de reglas detras que conteste
si esto falla. El modelo no recibe la clave de BUK ni el payload crudo, solo
puede llamar a las funciones declaradas en `herramientas.ESQUEMAS`.

Solo Gemini: es el proveedor con presupuesto aprobado. Hubo un respaldo con
OpenAI mientras se evaluaba, pero se saco del todo al confirmarse Gemini, para
no dejar una segunda ruta sin financiar a medio programar.
"""

import logging
import re

from django.conf import settings
from django.core.cache import cache

from . import herramientas

logger = logging.getLogger(__name__)

INSTRUCCIONES = """\
Eres Iris, la asistente interna de Azerta. Respondes sobre la informacion
operacional de la empresa: la nomina, la disponibilidad del equipo y las
politicas y procedimientos internos. Hoy es {hoy}.

Reglas:
- Responde SOLO con datos que devuelvan las herramientas. Si una herramienta no
  entrega el dato, di que no lo tienes. Nunca inventes nombres, fechas ni cifras.
- Si la pregunta nombra a una persona, usa `ausencias_de_persona`, no listes a
  todo el equipo.
- Nunca menciones el motivo o diagnostico de una licencia medica: es
  confidencial. Puedes decir que alguien esta con licencia medica y las fechas.
- Tono corporativo pero cercano: como un colega del area de Personas que
  responde rapido y bien. Trata de tu. Sin saludos de apertura ni relleno, pero
  tampoco telegrafico: una frase que contextualice antes del dato.
- Responde en espanol de Chile.
- Texto plano: nada de markdown, negritas ni asteriscos. La interfaz los muestra
  tal cual. Para enumerar personas usa una linea por persona con guion.
- Ante un saludo o una cortesia, responde con naturalidad en una linea y ofrece
  ayuda. No llames herramientas ni digas que te falta informacion.
- "Quien es X", "que cargo tiene X", "que cuentas maneja X" y "que clientes
  maneja X" son la misma familia de pregunta: usa `info_persona`, no
  `ausencias_de_persona`. "Quien es del equipo/cuenta de Y" y "muestrame el
  equipo que atiende Y" van con `equipo_de`. "Quien es el gerente/director/
  encargado de Y", cuando NO se nombra a una persona, va con
  `persona_por_cargo`.
- "Cuando cumple anos X" (con nombre) llama a `cumpleanos` con un rango
  amplio (por ejemplo dias=366) y busca a esa persona en el resultado; no
  respondas con la disponibilidad de X para esa pregunta.
- Tienes el historial de esta conversacion. Usalo para entender preguntas de
  seguimiento ("y sus vacaciones?", "y el segundo?") sin pedir que repitan el
  nombre.
- Si ninguna herramienta te da lo que piden, o el resultado dice
  "encontrada": false / "encontrado": false, NO improvises una respuesta
  parecida ni la contestes con generalidades: es preferible decir que no
  sabes. En ese caso, y SOLO en ese caso, tu respuesta debe empezar
  exactamente con "NO_SE:" (sin nada antes, ni siquiera un saludo), seguido
  de una frase breve. Ejemplo: "NO_SE: No tengo esa informacion todavia."
  Esta marca no la ve el usuario: el sistema la usa para registrar la
  pregunta y mejorar mas adelante. Nunca la uses si SI pudiste responder.
"""

MAX_TURNOS_HISTORIAL = 6  # 3 idas y vueltas: alcanza para el seguimiento sin
                          # inflar cada llamada con toda la conversacion.

MARCA_SIN_DATOS = "NO_SE:"


def _separar_exito(texto):
    """Quita la marca de "no se" y dice si el modelo pudo responder.

    Sin una marca explicita, "no tengo esa informacion" en texto libre es
    indistinguible de una respuesta real para el resto del sistema: no se
    podria registrar como consulta pendiente ni separarla en las metricas de
    una respuesta que si sirvio.
    """
    limpio = (texto or "").strip()
    if limpio.upper().startswith(MARCA_SIN_DATOS):
        return limpio[len(MARCA_SIN_DATOS):].strip(), False
    return limpio, True


class SinConfigurar(Exception):
    """No hay clave de API cargada para el proveedor elegido."""


# ---------------------------------------------------------------------------
# Cortacircuitos: si el proveedor esta caido o saturado, dejar de llamarlo un
# rato. Sin esto, cada pregunta espera el timeout completo antes de responder
# con las reglas, y el chat se siente colgado.
# ---------------------------------------------------------------------------

CLAVE_FALLAS = "asistente:fallas"
CLAVE_PAUSA = "asistente:pausa"
CLAVE_MOTIVO = "asistente:motivo"

# Por que dejo de responder, clasificado desde el mensaje de error del
# proveedor. Sirve para mostrarlo en la interfaz en vez de un generico "no
# disponible": no es lo mismo quedarse sin cuota gratis que tener la clave mal.
MOTIVOS_LEGIBLES = {
    "cuota_agotada": "se agotó la cuota gratuita por hoy",
    "prepago_agotado": "se agotaron los créditos prepagados en Google AI Studio",
    "clave_invalida": "la clave de API no es válida",
    "error_proveedor": "el proveedor no está respondiendo",
}


def _clasificar_error(error):
    texto = str(error or "")
    # Con facturacion activada, un 429 ya no es el limite gratis de 20/dia:
    # es que el saldo prepagado de la cuenta se vacio. Mismo codigo HTTP, aviso
    # bien distinto para quien tiene que ir a recargar.
    if "prepayment" in texto.lower() or "prepago" in texto.lower():
        return "prepago_agotado"
    if "RESOURCE_EXHAUSTED" in texto or "429" in texto:
        return "cuota_agotada"
    if any(p in texto for p in ("PERMISSION_DENIED", "API_KEY_INVALID", "401", "403")):
        return "clave_invalida"
    return "error_proveedor"


def en_pausa():
    return bool(cache.get(CLAVE_PAUSA))


def registrar_falla(error=None):
    fallas = (cache.get(CLAVE_FALLAS) or 0) + 1
    cache.set(CLAVE_FALLAS, fallas, settings.ASISTENTE_PAUSA_SEGUNDOS)
    # Se guarda desde la primera falla, no solo cuando se activa la pausa: si
    # la interfaz consulta el estado a mitad de una racha de fallas, ya hay un
    # motivo que mostrar en vez de nada.
    cache.set(CLAVE_MOTIVO, _clasificar_error(error), settings.ASISTENTE_PAUSA_SEGUNDOS)
    if fallas >= settings.ASISTENTE_FALLAS_MAX:
        cache.set(CLAVE_PAUSA, True, settings.ASISTENTE_PAUSA_SEGUNDOS)
        cache.delete(CLAVE_FALLAS)
        logger.warning(
            "modelo en pausa %ss tras %s fallas seguidas",
            settings.ASISTENTE_PAUSA_SEGUNDOS, fallas,
        )


def registrar_exito():
    cache.delete(CLAVE_FALLAS)
    cache.delete(CLAVE_PAUSA)
    cache.delete(CLAVE_MOTIVO)


def clave():
    return settings.GEMINI_API_KEY


def modelo():
    return settings.GEMINI_MODEL


def disponible():
    """Hay clave y el proveedor no esta en pausa por fallas recientes."""
    return bool(clave()) and not en_pausa()


def estado():
    """Para la interfaz: que modelo esta activo y, si no lo esta, por que.

    No expone nada nuevo que la interfaz no supiera ya (el nombre del modelo
    ya se manda en cada respuesta): solo lo junta en un solo lugar para
    pintarlo apenas se carga la pagina, sin esperar una pregunta.
    """
    if not clave():
        return {
            "disponible": False, "proveedor": "gemini", "modelo": modelo(),
            "motivo": "sin_clave",
            "motivo_legible": "no hay clave de API configurada para Gemini",
        }
    # Se guarda desde la primera falla, no solo cuando se activa la pausa: si
    # la ULTIMA pregunta fallo (créditos agotados, por ejemplo) el aviso en el
    # chat tiene que decir eso, aunque todavia no se hayan acumulado las
    # `ASISTENTE_FALLAS_MAX` seguidas que activan la pausa.
    motivo = cache.get(CLAVE_MOTIVO)
    if en_pausa():
        motivo = motivo or "error_proveedor"
        return {
            "disponible": False, "proveedor": "gemini", "modelo": modelo(),
            "motivo": motivo, "motivo_legible": MOTIVOS_LEGIBLES[motivo],
        }
    return {
        "disponible": True, "proveedor": "gemini", "modelo": modelo(),
        "motivo": motivo, "motivo_legible": MOTIVOS_LEGIBLES.get(motivo) if motivo else None,
    }


# --------------------------------------------------------------------------
# Anonimizacion (comun a ambos proveedores)
# --------------------------------------------------------------------------

def _anonimizar(resultado, alias):
    """Reemplaza nombres por alias antes de enviarlos al modelo.

    Protege la nomina: el modelo ve "Persona 3" en vez del nombre real. No
    protege el nombre que el propio usuario escribio en su pregunta, que viaja
    igual dentro del mensaje.
    """
    if isinstance(resultado, dict):
        salida = {}
        for clave_, valor in resultado.items():
            if clave_ in ("nombre", "candidatos"):
                if isinstance(valor, str):
                    salida[clave_] = alias.setdefault(valor, f"Persona {len(alias) + 1}")
                    continue
                if isinstance(valor, list):
                    salida[clave_] = [
                        alias.setdefault(v, f"Persona {len(alias) + 1}") for v in valor
                    ]
                    continue
            salida[clave_] = _anonimizar(valor, alias)
        return salida
    if isinstance(resultado, list):
        return [_anonimizar(v, alias) for v in resultado]
    return resultado


def _restaurar(texto, alias):
    """Devuelve los nombres reales al texto que produjo el modelo."""
    for real, seudonimo in sorted(alias.items(), key=lambda kv: -len(kv[1])):
        texto = re.sub(re.escape(seudonimo), real, texto, flags=re.IGNORECASE)
    return texto


def _ejecutar(nombre, argumentos, alias, llamadas):
    """Corre una herramienta y devuelve el resultado listo para el modelo."""
    funcion = herramientas.FUNCIONES.get(nombre)
    if funcion is None:
        resultado = {"error": f"La herramienta {nombre} no existe."}
    else:
        try:
            resultado = funcion(**argumentos)
        except Exception as error:  # la herramienta falla, no la conversacion
            logger.warning("herramienta %s fallo: %s", nombre, error)
            resultado = {"error": str(error)}
    llamadas.append({"nombre": nombre, "argumentos": argumentos})
    if settings.ASISTENTE_ANONIMIZAR:
        return _anonimizar(resultado, alias)
    return resultado


# --------------------------------------------------------------------------
# Gemini
# --------------------------------------------------------------------------

def _cliente_gemini():
    from google import genai
    from google.genai import types

    # Sin timeout explicito una llamada colgada deja la pregunta esperando para
    # siempre: el SDK no impone limite por su cuenta.
    return genai.Client(
        api_key=settings.GEMINI_API_KEY,
        http_options=types.HttpOptions(timeout=settings.ASISTENTE_TIMEOUT * 1000),
    )


def _declaraciones_gemini():
    """Traduce `herramientas.ESQUEMAS` (JSON Schema generico) al formato de
    Google."""
    from google.genai import types

    funciones = [
        types.FunctionDeclaration(
            name=e["function"]["name"],
            description=e["function"]["description"],
            parameters_json_schema=e["function"]["parameters"],
        )
        for e in herramientas.ESQUEMAS
    ]
    return [types.Tool(function_declarations=funciones)]


def _responder_gemini(mensaje, hoy, historial_previo=None, alias=None):
    from google.genai import types

    cliente = _cliente_gemini()
    config = types.GenerateContentConfig(
        system_instruction=INSTRUCCIONES.format(hoy=hoy.isoformat()),
        tools=_declaraciones_gemini(),
        temperature=0,
        # El bucle lo controlamos nosotros: la ejecucion automatica saltaria la
        # anonimizacion y el registro de que herramientas se usaron.
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )

    # El alias se reutiliza entre turnos: si no, la misma persona cambiaria de
    # seudonimo a mitad de conversacion y el modelo perderia el hilo.
    if alias is None:
        alias = {}
    llamadas, pasos = [], 0
    # Solo texto final de turnos anteriores, no las llamadas a herramientas
    # intermedias: alcanza para el seguimiento y evita cargar cada vez la
    # firma de pensamiento de vueltas ya cerradas.
    historial = [
        types.Content(role=turno["role"], parts=[types.Part.from_text(text=turno["texto"])])
        for turno in (historial_previo or [])
    ]
    mensaje_actual = mensaje
    historial.append(types.Content(role="user", parts=[types.Part.from_text(text=mensaje_actual)]))

    while pasos < settings.ASISTENTE_MAX_PASOS:
        pasos += 1
        respuesta = cliente.models.generate_content(
            model=settings.GEMINI_MODEL, contents=historial, config=config
        )
        pedidos = respuesta.function_calls or []

        if not pedidos:
            texto = (respuesta.text or "").strip()
            if settings.ASISTENTE_ANONIMIZAR:
                texto = _restaurar(texto, alias)
            texto, exitosa = _separar_exito(texto)
            nuevo_historial = (historial_previo or []) + [
                {"role": "user", "texto": mensaje_actual},
                {"role": "model", "texto": texto},
            ]
            meta = {"pasos": pasos, "herramientas": llamadas, "exitosa": exitosa,
                    "historial": nuevo_historial[-MAX_TURNOS_HISTORIAL:], "alias": alias}
            return texto, meta

        # Se devuelve el contenido original del modelo, sin reconstruirlo: los
        # modelos Gemini 3.x firman cada functionCall con un `thought_signature`
        # y exigen recibirlo de vuelta. Rearmar las partes a mano lo pierde y la
        # API responde 400 INVALID_ARGUMENT.
        candidatos = respuesta.candidates or []
        if candidatos and candidatos[0].content:
            historial.append(candidatos[0].content)
        else:
            historial.append(types.Content(
                role="model",
                parts=[types.Part.from_function_call(name=p.name, args=dict(p.args or {}))
                       for p in pedidos],
            ))
        respuestas_tool = []
        for pedido in pedidos:
            resultado = _ejecutar(pedido.name, dict(pedido.args or {}), alias, llamadas)
            respuestas_tool.append(
                types.Part.from_function_response(name=pedido.name, response=resultado)
            )
        historial.append(types.Content(role="user", parts=respuestas_tool))

    return None, {"pasos": pasos, "herramientas": llamadas, "agotado": True,
                  "historial": historial_previo or [], "alias": alias}


def responder(mensaje, hoy, historial=None, alias=None):
    """Devuelve (texto, meta). Lanza SinConfigurar o el error de Gemini.

    `historial` y `alias` son la memoria de la conversacion (ver `views.py`).
    """
    if not clave():
        raise SinConfigurar("No hay clave para Gemini. Configura GEMINI_API_KEY en .env")
    try:
        resultado = _responder_gemini(mensaje, hoy, historial, alias)
    except Exception as error:
        registrar_falla(error)
        raise
    registrar_exito()
    return resultado
