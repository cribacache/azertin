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
# Los apodos se indexan desde dos letras porque "JM" y "Jo" son apodos reales;
# las palabras cortas del idioma quedan en RESERVADAS para que no interfieran.
LARGO_MINIMO_APODO = 2
# Cuanto se tiene que parecer una palabra a un nombre real para sugerirla.
# Alto a proposito: "garrid" debe sugerir "Garrido", pero "pinilla" no tiene por
# que sugerir "Padilla" solo porque comparten letras.
PARECIDO = 0.78


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


def _vocabulario():
    """Palabras que ya sabemos que son de la pregunta, no nombres mal escritos.

    Sin esto "años" sugiere "Llanos" y "¿quien cumple años?" termina
    respondiendo por una persona.
    """
    from . import intents

    palabras = set()
    fuentes = [intents.PALABRAS_AUSENCIA, intents.PALABRAS_CUMPLE,
               intents.PALABRAS_TRABAJANDO, intents.PALABRAS_DOTACION,
               intents.PALABRAS_PROCEDIMIENTO, intents.PALABRAS_COMPLEJAS,
               intents.SALUDOS, intents.AGRADECIMIENTOS, intents.DESPEDIDAS,
               intents.IDENTIDAD, intents.PALABRAS_IDENTIDAD_PERSONA, intents.MESES]
    for fuente in fuentes:
        for frase in fuente:
            palabras.update(_tokens(frase))
    for _, sinonimos in intents.CATEGORIA_POR_PALABRA:
        for frase in sinonimos:
            palabras.update(_tokens(frase))
    return palabras


def sugerir(mensaje, directorio, limite=3):
    """Nombres parecidos a una palabra del mensaje que no coincide con nadie.

    Para los errores de tipeo: "felipe garrid" o "javiera morno". Solo se
    consideran palabras que se parezcan mucho a un nombre real, asi que una
    palabra cualquiera de la pregunta no dispara sugerencias.
    """
    import difflib

    mapa = indice(directorio)
    conocidos = list(mapa)
    vocabulario = _vocabulario()
    sugeridos = {}
    for token in _tokens(mensaje):
        if (len(token) < LARGO_MINIMO or token in mapa
                or token in RESERVADAS or token in vocabulario):
            continue
        cercanos = difflib.get_close_matches(token, conocidos, n=2, cutoff=PARECIDO)
        for cercano in cercanos:
            for pid in mapa[cercano]:
                sugeridos.setdefault(pid, token)

    return list(sugeridos)[:limite]


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


# Palabras despues de las cuales suele venir un cargo, no un nombre: "quien es
# el gerente de personas" no nombra a nadie, pregunta quien ocupa ese cargo.
_INTRO_CARGO = ("quien es", "quien era", "quien fue", "sabes quien es")


def buscar_por_cargo(mensaje, directorio):
    """Ids de quienes tienen el cargo que se pregunta, o set() si no aplica.

    Solo se activa con la forma "quien es el/la <cargo>": buscar por cargo en
    cualquier otro tipo de pregunta daria falsos positivos (una palabra
    cualquiera de la pregunta podria coincidir con un cargo real).
    """
    texto = normalizar(mensaje).strip(" ?¿.,")
    intro = next((i for i in _INTRO_CARGO if texto.startswith(i)), None)
    if intro is None:
        return set()

    resto = texto[len(intro):].strip()
    for articulo in ("el ", "la ", "los ", "las "):
        if resto.startswith(articulo):
            resto = resto[len(articulo):]
            break

    palabras = {p for p in resto.split() if len(p) >= 3}
    if not palabras:
        return set()

    coincidencias = set()
    for pid, persona in directorio.items():
        cargo = set(normalizar(persona.get("cargo") or "").split())
        if palabras <= cargo:
            coincidencias.add(pid)
    return coincidencias
