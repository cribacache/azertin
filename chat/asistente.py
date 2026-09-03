"""Capa de lenguaje natural sobre las herramientas de `chat/herramientas.py`.

Se usa solo como respaldo: lo que el router de reglas ya entiende se responde
sin gastar tokens. El modelo no recibe la clave de BUK ni el payload crudo, solo
puede llamar a las funciones declaradas en `herramientas.ESQUEMAS`.

Hay dos proveedores. Lo unico que cambia entre ellos es el bucle de llamadas:
las herramientas, el prompt y la anonimizacion son los mismos.
"""

import json
import logging
import re

from django.conf import settings

from . import herramientas

logger = logging.getLogger(__name__)

INSTRUCCIONES = """\
Eres azertin, el asistente interno de Azerta. Respondes sobre la informacion
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
- Si no puedes responder con las herramientas, dilo claramente en una frase.
"""


class SinConfigurar(Exception):
    """No hay clave de API cargada para el proveedor elegido."""


def proveedor():
    return (settings.ASISTENTE_PROVEEDOR or "gemini").lower()


def clave():
    return settings.GEMINI_API_KEY if proveedor() == "gemini" else settings.OPENAI_API_KEY


def modelo():
    return settings.GEMINI_MODEL if proveedor() == "gemini" else settings.OPENAI_MODEL


def disponible():
    return bool(clave())


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
    """Reutiliza los mismos esquemas JSON que usa OpenAI."""
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


def _responder_gemini(mensaje, hoy):
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

    historial = [types.Content(role="user", parts=[types.Part.from_text(text=mensaje)])]
    alias, llamadas, pasos = {}, [], 0

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
            return texto, {"pasos": pasos, "herramientas": llamadas}

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

    return None, {"pasos": pasos, "herramientas": llamadas, "agotado": True}


# --------------------------------------------------------------------------
# OpenAI
# --------------------------------------------------------------------------

def _cliente_openai():
    from openai import OpenAI

    return OpenAI(api_key=settings.OPENAI_API_KEY, timeout=settings.ASISTENTE_TIMEOUT)


def _responder_openai(mensaje, hoy):
    cliente = _cliente_openai()
    alias, llamadas, pasos = {}, [], 0
    mensajes = [
        {"role": "system", "content": INSTRUCCIONES.format(hoy=hoy.isoformat())},
        {"role": "user", "content": mensaje},
    ]

    while pasos < settings.ASISTENTE_MAX_PASOS:
        pasos += 1
        respuesta = cliente.chat.completions.create(
            model=settings.OPENAI_MODEL,
            messages=mensajes,
            tools=herramientas.ESQUEMAS,
            tool_choice="auto",
            temperature=0,
        )
        eleccion = respuesta.choices[0].message

        if not eleccion.tool_calls:
            texto = (eleccion.content or "").strip()
            if settings.ASISTENTE_ANONIMIZAR:
                texto = _restaurar(texto, alias)
            return texto, {"pasos": pasos, "herramientas": llamadas}

        mensajes.append(eleccion.model_dump(exclude_none=True))
        for llamada in eleccion.tool_calls:
            try:
                argumentos = json.loads(llamada.function.arguments or "{}")
            except json.JSONDecodeError:
                argumentos = {}
            resultado = _ejecutar(llamada.function.name, argumentos, alias, llamadas)
            mensajes.append({
                "role": "tool",
                "tool_call_id": llamada.id,
                "content": json.dumps(resultado, ensure_ascii=False, default=str),
            })

    return None, {"pasos": pasos, "herramientas": llamadas, "agotado": True}


def responder(mensaje, hoy):
    """Devuelve (texto, meta). Lanza SinConfigurar o el error del proveedor."""
    if not disponible():
        raise SinConfigurar(
            f"No hay clave para {proveedor()}. Configura "
            f"{'GEMINI_API_KEY' if proveedor() == 'gemini' else 'OPENAI_API_KEY'} en .env"
        )
    if proveedor() == "gemini":
        return _responder_gemini(mensaje, hoy)
    return _responder_openai(mensaje, hoy)
