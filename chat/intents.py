"""Traduce el mensaje del usuario a una consulta acotada contra BUK.

La deteccion es por palabras clave, no por LLM: alcanza para el caso de uso y
mantiene la respuesta en milisegundos. Lo importante es que el resultado sea un
filtro estrecho (tipo + rango de fechas) para no pedirle a BUK mas de lo justo.
"""

import re
import unicodedata
from datetime import date, timedelta

MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}

CATEGORIA_POR_PALABRA = (
    ("vacaciones", ("vacacion", "vacaciones", "feriado legal", "feriados legales",
                    "dia administrativo", "dias administrativos", "administrativo")),
    ("licencia", ("licencia", "licencias", "medica", "medicas", "enfermo", "enferma")),
    ("permiso", ("permiso", "permisos")),
    ("inasistencia", ("inasistencia", "inasistencias", "falto", "faltaron", "falta",
                      "faltas", "no llego", "no llegaron")),
)

PALABRAS_AUSENCIA = (
    "ausencia", "ausencias", "ausente", "ausentes", "fuera", "no esta",
    "no estan", "quien falta", "descanso", "jornada", "no viene", "no vienen",
    "no vino", "no vinieron", "no puedo contar", "disponible", "disponibles",
)

# Marcas de que se pregunta por un procedimiento, no por quien esta fuera.
# "como pido vacaciones" comparte la palabra "vacaciones" con "quien esta de
# vacaciones", pero la respuesta correcta esta en los documentos, no en BUK.
PALABRAS_PROCEDIMIENTO = (
    "como ", "como?", "a quien", "donde ", "que pasa si", "puedo ", "podria ",
    "debo ", "tengo que", "hay que", "se puede", "se pide", "se solicita",
    "procedimiento", "politica", "reglamento", "requisito", "requisitos",
    "plazo", "anticipacion", "quien autoriza", "quien aprueba", "me toca",
    "me corresponde", "sirve", "significa", "que son", "en que consiste",
    "que es el", "que es la", "que es un", "diferencia entre",
)


# Preguntas que las reglas NO saben resolver: comparaciones, agregaciones,
# filtros por area, causas. El router las reconoceria a medias y contestaria una
# lista equivocada, que es peor que no contestar.
PALABRAS_COMPLEJAS = (
    "compara", "comparar", "comparacion", " vs ", "versus", "contra ",
    "diferencia", "por que", "porque", "que area", "cual area", "por area",
    "por equipo", "por cargo", "agrupa", "agrupado", "promedio", "ranking",
    "tendencia", "analiza", "analisis", "explica", "resumen", "cuantos dias",
    "cuanto tiempo", "mas gente", "mas personas", "quien mas", "cuales son los",
)

# Familias de rol y areas reales de la nomina. Filtrar por ellas exige cruzar
# ausencias con el cargo, que el router no hace: preguntarlo sin modelo
# devolveria el total de la empresa como si fuera el del area.
PALABRAS_AREA = (
    "comunicaciones", "asuntos publicos", "digital", "directores", "consultores",
    "ejecutivos", "gerentes", "socios", "del area", "de mi area",
    "del equipo de", "departamento",
)


def es_compleja(mensaje):
    """True si la pregunta excede lo que el router puede contestar bien."""
    texto = normalizar(mensaje)
    return any(p in texto for p in PALABRAS_COMPLEJAS + PALABRAS_AREA)


def es_procedimiento(mensaje):
    """True si la pregunta es sobre una regla y no sobre quien esta ausente.

    Las marcas de ausencia mandan: "con quien no puedo contar" contiene "puedo",
    pero pregunta por personas, no por el procedimiento.
    """
    texto = normalizar(mensaje)
    if any(p in texto for p in PALABRAS_AUSENCIA):
        return False
    return any(p in texto for p in PALABRAS_PROCEDIMIENTO)


# Cortesia y presentaciones. No necesitan datos ni modelo: gastarle una llamada
# a Gemini para responder "Hola" cuesta cuota y segundos de espera, y si el
# proveedor esta caido termina contestando "no tengo esa informacion" a un saludo.
SALUDOS = ("hola", "holi", "buenas", "buen dia", "buenos dias", "buenas tardes",
           "buenas noches", "hey", "que tal", "como estas", "como andas",
           "como va", "que hay")

AGRADECIMIENTOS = ("gracias", "muchas gracias", "te pasaste", "genial", "perfecto",
                   "buenisimo", "excelente", "dale gracias")

DESPEDIDAS = ("chao", "chau", "adios", "hasta luego", "nos vemos", "bye",
              "hasta manana", "buen fin de semana")

IDENTIDAD = ("quien eres", "que eres", "como te llamas", "que puedes hacer",
             "que sabes hacer", "en que me puedes ayudar", "para que sirves",
             "que haces", "en que ayudas", "ayuda")


def _coincide(texto, frases):
    """Coincidencia por palabra completa, para que "ayuda" no capture
    "ayudame con las vacaciones de octubre"."""
    palabras = " " + " ".join(
        "".join(c if c.isalnum() else " " for c in texto).split()
    ) + " "
    return any(f" {f} " in palabras for f in frases)


def detectar_cortesia(texto):
    if _coincide(texto, SALUDOS):
        return "saludo"
    if _coincide(texto, AGRADECIMIENTOS):
        return "gracias"
    if _coincide(texto, DESPEDIDAS):
        return "despedida"
    if _coincide(texto, IDENTIDAD):
        return "identidad"
    return None


PALABRAS_DOTACION = ("cuantas personas", "cuantos empleados", "dotacion", "headcount", "nomina")


def normalizar(texto):
    # NFKD y no NFD: los PDF exportados traen ligaduras (ﬁ, ﬂ) que NFD deja
    # intactas, y entonces "planiﬁcacion" nunca coincide con "planificacion".
    texto = unicodedata.normalize("NFKD", texto.lower())
    return "".join(c for c in texto if unicodedata.category(c) != "Mn")


def _semana(hoy):
    lunes = hoy - timedelta(days=hoy.weekday())
    return lunes, lunes + timedelta(days=6)


def _fin_de_mes(anio, mes):
    if mes == 12:
        return date(anio, 12, 31)
    return date(anio, mes + 1, 1) - timedelta(days=1)


def detectar_rango(texto, hoy):
    """Devuelve (desde, hasta, etiqueta). Por defecto, hoy."""
    # Fecha explicita: 2026-09-07 o 07-09-2026 / 07/09/2026
    iso = re.search(r"(\d{4})-(\d{2})-(\d{2})", texto)
    if iso:
        d = date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
        return d, d, f"el {d.isoformat()}"

    dmy = re.search(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b", texto)
    if dmy:
        d = date(int(dmy.group(3)), int(dmy.group(2)), int(dmy.group(1)))
        return d, d, f"el {d.isoformat()}"

    if "pasado manana" in texto:
        d = hoy + timedelta(days=2)
        return d, d, "pasado manana"
    if "manana" in texto:
        d = hoy + timedelta(days=1)
        return d, d, "manana"
    if "ayer" in texto:
        d = hoy - timedelta(days=1)
        return d, d, "ayer"
    if "proxima semana" in texto or "siguiente semana" in texto:
        lunes, domingo = _semana(hoy + timedelta(days=7))
        return lunes, domingo, "la proxima semana"
    if "esta semana" in texto or "semana" in texto:
        lunes, domingo = _semana(hoy)
        return lunes, domingo, "esta semana"
    if "proximo mes" in texto:
        anio, mes = (hoy.year + 1, 1) if hoy.month == 12 else (hoy.year, hoy.month + 1)
        return date(anio, mes, 1), _fin_de_mes(anio, mes), "el proximo mes"
    if "este mes" in texto:
        return date(hoy.year, hoy.month, 1), _fin_de_mes(hoy.year, hoy.month), "este mes"

    for nombre, mes in MESES.items():
        if re.search(rf"\b{nombre}\b", texto):
            anio = hoy.year
            anio_txt = re.search(r"\b(20\d{2})\b", texto)
            pasado = any(v in texto for v in ("estuvo", "estuvieron", "hubo", "fue",
                                              "fueron", "paso", "pasado", "tuvo"))
            if anio_txt:
                anio = int(anio_txt.group(1))
            elif mes < hoy.month and not pasado:
                anio += 1
            return date(anio, mes, 1), _fin_de_mes(anio, mes), f"en {nombre} de {anio}"

    return hoy, hoy, "hoy"


SUBTIPO_POR_PALABRA = (
    ("dias_administrativos", ("dia administrativo", "dias administrativos", "administrativo",
                              "administrativos")),
    ("legales", ("feriado legal", "feriados legales", "vacaciones legales")),
    ("progresivas", ("progresiva", "progresivas")),
)


def detectar_subtipo(texto):
    for subtipo, palabras in SUBTIPO_POR_PALABRA:
        if any(p in texto for p in palabras):
            return subtipo
    return None


def detectar_categoria(texto):
    for categoria, palabras in CATEGORIA_POR_PALABRA:
        if any(p in texto for p in palabras):
            return categoria
    return None


def interpretar(mensaje, hoy=None):
    """Devuelve un dict con la intencion y los filtros a aplicar."""
    hoy = hoy or date.today()
    texto = normalizar(mensaje)

    categoria = detectar_categoria(texto)
    pregunta_ausencia = categoria is not None or any(p in texto for p in PALABRAS_AUSENCIA)

    # "cuantas personas hay activas" es dotacion; "cuantas personas de
    # comunicaciones estan fuera" no lo es, aunque comparta las palabras.
    if any(p in texto for p in PALABRAS_DOTACION) and not pregunta_ausencia:
        return {"intencion": "dotacion"}

    if pregunta_ausencia:
        desde, hasta, etiqueta = detectar_rango(texto, hoy)
        return {
            "intencion": "ausencias",
            "categoria": categoria,
            "subtipo": detectar_subtipo(texto) if categoria == "vacaciones" else None,
            "desde": desde,
            "hasta": hasta,
            "etiqueta": etiqueta,
        }

    cortesia = detectar_cortesia(texto)
    if cortesia:
        return {"intencion": cortesia}

    return {"intencion": "ayuda"}
