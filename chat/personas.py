"""Encuentra a que persona de la nomina se refiere una pregunta.

No usa el nombre completo: la gente pregunta "esta Javiera?" o "cuando vuelve
Duk". Se buscan coincidencias de nombre o apellido contra el directorio y se
distingue entre no encontrar a nadie y encontrar a varios, porque el asistente
tiene que responder distinto en cada caso.
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


def _tokens(texto):
    limpio = "".join(c if c.isalnum() else " " for c in normalizar(texto))
    return [t for t in limpio.split() if t]


def indice(directorio):
    """Mapa {token de nombre: {ids}} a partir del directorio."""
    mapa = {}
    for pid, persona in directorio.items():
        for token in _tokens(persona.get("nombre", "")):
            if len(token) >= LARGO_MINIMO and token not in RESERVADAS:
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
