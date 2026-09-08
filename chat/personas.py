"""Encuentra a que persona de la nomina se refiere una pregunta.

No usa el nombre completo: la gente pregunta "esta Javiera?" o "cuando vuelve
Duk". Se buscan coincidencias de nombre o apellido contra el directorio y se
distingue entre no encontrar a nadie y encontrar a varios: `chat/herramientas.py`
responde distinto en cada caso al armar el resultado que recibe Gemini.
"""

from .intents import normalizar

# Palabras del vocabulario de preguntas que tambien podrian ser apellidos.
# Sin esto, "quien falta hoy" activaria a alguien apellidado Falta.
RESERVADAS = {
    # particulas de nombres compuestos
    "del", "las", "los", "san", "santa", "de", "la", "el",
    # apellidos reales de la nomina que tambien son palabras del vocabulario
    "falta", "faltas", "casas", "campos", "vega", "leon", "cruz", "mayo",
    "vacaciones", "licencia", "permiso", "jornada", "semana", "lunes", "marzo",
    "abril", "junio", "julio", "agosto", "octubre", "salgado",
}

# Tres letras porque hay nombres reales asi de cortos (Ana, Paz, Ian) y
# apellidos por los que la gente pregunta (Duk).
LARGO_MINIMO = 3
# Los apodos se indexan desde dos letras porque "JM" y "Jo" son apodos reales;
# las palabras cortas del idioma quedan en RESERVADAS para que no interfieran.
LARGO_MINIMO_APODO = 2


def _tokens(texto):
    limpio = "".join(c if c.isalnum() else " " for c in normalizar(texto))
    return [t for t in limpio.split() if t]


def indice(directorio):
    """Mapa {token: {ids}} con nombres y apodos.

    La gente pregunta por el apodo mucho mas que por el nombre completo: "esta
    la Mane?" es mas comun que "esta Maria Jose Pena Gutierrez?".
    """
    from .buk import apodos_de

    mapa = {}
    for pid, persona in directorio.items():
        textos = [persona.get("nombre", "")] + apodos_de(persona.get("apodo"))
        for texto in textos:
            for token in _tokens(texto):
                # los apodos son cortos ("Eli", "JM"), asi que se permite menos
                # largo que en los nombres, pero nunca menos de tres letras
                minimo = LARGO_MINIMO_APODO if texto != persona.get("nombre", "") else LARGO_MINIMO
                if len(token) >= minimo and token not in RESERVADAS:
                    mapa.setdefault(token, set()).add(pid)
    return mapa


def buscar(mensaje, directorio):
    """Devuelve (ids_encontrados, tokens_usados).

    Un solo id significa una persona identificada; varios, un nombre ambiguo.
    """
    mapa = indice(directorio)
    tokens = [t for t in _tokens(mensaje) if t in mapa]
    if not tokens:
        return set(), []

    # Si varios tokens apuntan a la misma persona ("javiera moreno"), la
    # interseccion la identifica. Si no se cruzan, se ofrecen todos.
    candidatos = None
    for token in tokens:
        candidatos = mapa[token] if candidatos is None else candidatos & mapa[token]
        if not candidatos:
            candidatos = None
            break

    if candidatos:
        return set(candidatos), tokens

    union = set()
    for token in tokens:
        union |= mapa[token]
    return union, tokens
