import json
import logging
import re
from collections import Counter
from datetime import date

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST

from . import asistente, buk, cuentas, documentos, intents, personas, respuestas
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
        "nombre": (persona.get("nombre_completo") or persona.get("nombre")
                   or f"Empleado #{registro['employee_id']}"),
        "cargo": persona.get("cargo") or "",
        "area": persona.get("area") or "",
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
    nombre = persona.get("nombre_completo") or persona.get("nombre", f"Empleado #{pid}")
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


def _familias(personas_map):
    return {p["familia"] for p in personas_map.values() if p.get("familia")}


def _filtrar_familia(gente, familia):
    return [p for p in gente if (p.get("familia") or "") == familia]


def _agrupar_por_persona(items):
    """Una fila por persona y tipo de ausencia.

    Quien parte sus vacaciones en tres tramos aparecia tres veces seguidas. Se
    agrupa por (persona, tipo) y no solo por persona: alguien puede tener
    vacaciones y licencia a la vez, y fusionarlas perderia una de las dos.
    """
    por_persona = {}
    for item in items:
        por_persona.setdefault((item["id"], item["tipo"]), []).append(item)

    salida = []
    for tramos in por_persona.values():
        tramos.sort(key=lambda i: i["desde"] or "")
        primero = dict(tramos[0])
        if len(tramos) > 1:
            primero["detalle"] = " · ".join(
                x for x in (primero.get("detalle"), f"+{len(tramos) - 1} tramos") if x)
        salida.append(primero)
    salida.sort(key=lambda i: (i["desde"] or "", i["nombre"]))
    return salida


def _filtrar_area(items, area):
    if not area:
        return items
    clave = intents.normalizar(area)
    return [i for i in items if clave in intents.normalizar(i.get("area") or i.get("cargo") or "")]


def _filtrar_cuenta(gente, cuenta):
    """Se compara por nombre de cuenta, que es lo que quedo en el directorio."""
    return [p for p in gente if cuenta in (p.get("cuentas") or [])]


def _grupo_no_disponible(mensaje, personas_map, req):
    """Aviso cuando se pregunta por un grupo que no es ni area ni cuenta."""
    nombres = {p["area"] for p in personas_map.values() if p.get("area")}
    grupo = intents.detectar_grupo_desconocido(intents.normalizar(mensaje), nombres)
    if not grupo or cuentas.buscar(mensaje) is not None:
        return None
    registrar(mensaje, "sin_datos")
    return {
        "answer": (
            f"No encuentro «{grupo}» ni entre las áreas ni entre las cuentas. "
            f"Las áreas son: {', '.join(sorted(nombres))}. "
            f"Las cuentas están en la planilla de asignación; si la cuenta es "
            f"nueva, hay que actualizarla ahí."
        ),
        "items": [],
        "meta": {"intencion": "grupo_desconocido", "grupo": grupo, "requests_buk": req},
    }


def _numerar(opciones):
    return "\n".join(f"{i}. {o}" for i, o in enumerate(opciones, 1))


def responder_ambiguo(ids, personas_map, mensaje, req):
    """Varios coinciden con el nombre o apodo: se pregunta cual.

    Se numeran para que se pueda responder "2" y no haya que escribir el nombre
    completo, y se deja el contexto pendiente para resolverlo en el mensaje
    siguiente.
    """
    registrar(mensaje, "persona_ambigua")
    orden = sorted(ids, key=lambda i: personas_map[i].get("nombre_completo")
                   or personas_map[i]["nombre"])[:8]
    nombres = [personas_map[i].get("nombre_completo") or personas_map[i]["nombre"]
               for i in orden]
    return {
        "answer": (f"Hay {len(nombres)} personas que coinciden. ¿Por cuál preguntas?\n"
                   + _numerar(nombres)
                   + "\n\nRespóndeme con el número o con el apellido."),
        "items": [],
        "meta": {"intencion": "persona_ambigua", "requests_buk": req},
        "_pendiente": {"tipo": "persona", "ids": list(orden), "opciones": nombres,
                       "pregunta": mensaje},
    }


def responder_cuenta_ambigua(opciones, mensaje):
    """Varias cuentas comparten la palabra usada ("AFP")."""
    registrar(mensaje, "sin_datos")
    return {
        "answer": (f"Hay {len(opciones)} cuentas que coinciden. ¿Cuál de ellas?\n"
                   + _numerar(opciones)
                   + "\n\nRespóndeme con el número o con el nombre."),
        "items": [],
        "meta": {"intencion": "cuenta_ambigua", "requests_buk": 0},
        "_pendiente": {"tipo": "cuenta", "opciones": list(opciones),
                       "pregunta": mensaje},
    }


AFIRMACIONES = ("si", "sip", "claro", "exacto", "correcto", "ese", "esa",
                "dale", "obvio", "asi es", "el mismo", "la misma")


def elegir_opcion(respuesta, opciones):
    """Indice elegido en una respuesta como "2", "la segunda" o "Moreno".

    Devuelve None si no queda claro: insistir con la pregunta es mejor que
    adivinar cual de cuatro personas queria.
    """
    texto = intents.normalizar(respuesta).strip()

    # con una sola opcion, un "si" alcanza para confirmar
    if len(opciones) == 1:
        palabras = set(re.split(r"[^\w]+", texto))
        if palabras & set(AFIRMACIONES):
            return 0

    numero = re.search(r"\b(\d{1,2})\b", texto)
    if numero:
        indice = int(numero.group(1)) - 1
        if 0 <= indice < len(opciones):
            return indice

    ordinales = ("primera", "segunda", "tercera", "cuarta", "quinta",
                 "sexta", "septima", "octava")
    for i, palabra in enumerate(ordinales):
        if palabra in texto and i < len(opciones):
            return i

    # por palabra del nombre: entre pocas opciones, "Moreno" ya distingue
    palabras = {p for p in re.split(r"[^\w]+", texto) if len(p) >= 3}
    coinciden = [i for i, o in enumerate(opciones)
                 if palabras & set(intents.normalizar(o).split())]
    return coinciden[0] if len(coinciden) == 1 else None


def responder_ausencias(plan):
    categoria, etiqueta = plan.get("categoria"), plan["etiqueta"]
    subtipo = plan.get("subtipo")
    personas_map, req_dir = buk.directorio()
    aviso = _grupo_no_disponible(plan["mensaje"], personas_map, req_dir)
    if aviso:
        return aviso
    area = intents.detectar_area(intents.normalizar(plan["mensaje"]),
                                 {p["area"] for p in personas_map.values() if p.get("area")})

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
    cuenta = cuentas.buscar(plan["mensaje"])
    if cuenta and "ambiguas" in cuenta:
        return responder_cuenta_ambigua(cuenta["ambiguas"], plan["mensaje"])
    if cuenta:
        ids = {p["id"] for p in personas_map.values()
               if cuenta["nombre"] in (p.get("cuentas") or [])}
        items = [i for i in items if i["id"] in ids]
        etiqueta = f"{etiqueta} en {cuenta['nombre']}"
    elif area:
        items = _filtrar_area(items, area)
        etiqueta = f"{etiqueta} en {area}"

    familia = intents.detectar_familia(intents.normalizar(plan["mensaje"]),
                                       _familias(personas_map))
    if familia:
        ids_fam = {p["id"] for p in personas_map.values() if p.get("familia") == familia}
        items = [i for i in items if i["id"] in ids_fam]
        etiqueta = f"{etiqueta}, {familia.lower()}"

    if categoria:
        nombre = buk.CATEGORIAS[categoria]["etiqueta"]
        if subtipo:
            nombre = buk.TIPOS_VACACION_PLURAL.get(subtipo, nombre)
        # se cuentan PERSONAS y no registros: quien parte sus vacaciones en
        # tres tramos aparece tres veces en la lista, pero es una sola persona
        distintas = len({i["id"] for i in items})
        if not items:
            texto = f"No hay nadie con {nombre} {etiqueta}."
        elif distintas == 1:
            i = items[0]
            texto = (f"Hay 1 persona con {nombre} {etiqueta}: {i['nombre']}, "
                     f"del {i['desde']} al {i['hasta']}.")
        else:
            texto = f"Hay {distintas} personas con {nombre} {etiqueta}. Te las dejo abajo."
    elif not items:
        texto = f"El equipo está completo {etiqueta}: nadie registra ausencias."
    else:
        distintas = len({i["id"] for i in items})
        resumen = Counter()
        for tipo in {(i["id"], i["tipo"]) for i in items}:
            resumen[tipo[1]] += 1
        detalle = ", ".join(f"{n} con {t}" for t, n in resumen.most_common())
        plural = "persona" if distintas == 1 else "personas"
        texto = f"Hay {distintas} {plural} fuera de su jornada {etiqueta}: {detalle}."

    return {
        "answer": texto,
        "items": _agrupar_por_persona(items),
        "meta": {
            "intencion": "ausencias",
            "categoria": categoria or "todas",
            "subtipo": subtipo,
            "rango": f"{plan['desde']} a {plan['hasta']}",
            "requests_buk": req + req_dir,
        },
    }


# Se devuelve el mismo saludo que uso la persona: responder "Hola" a un
# "buenas tardes" suena a formulario, no a asistente.
SALUDO_ESPEJO = (
    ("buenas noches", "Buenas noches."),
    ("buenas tardes", "Buenas tardes."),
    ("buenos dias", "Buenos días."),
    ("buen dia", "Buenos días."),
)

CORTESIA = {
    "saludo": "Hola.",
    "gracias": "De nada. Cualquier otra cosa que necesites, aquí estoy.",
    "despedida": "Hasta luego.",
    "identidad": (
        "Soy Iris, el asistente interno de Azerta. Reúno la información "
        "operacional de la empresa para que no tengas que ir a buscarla."
    ),
}

QUE_PUEDO = (
    "Puedo decirte quién está fuera de su jornada —vacaciones, licencias, "
    "permisos o inasistencias—, revisar la situación de una persona en "
    "particular y consultar las políticas internas."
)


def responder_cortesia(intencion, mensaje=""):
    """Saludos y cortesías: instantáneo, sin BUK y sin gastar modelo."""
    texto = CORTESIA[intencion]
    if intencion == "saludo":
        normalizado = intents.normalizar(mensaje)
        for clave, saludo in SALUDO_ESPEJO:
            if clave in normalizado:
                texto = saludo
                break
        texto = f"{texto} ¿En qué te ayudo? {QUE_PUEDO}"
    elif intencion == "identidad":
        texto = f"{texto} {QUE_PUEDO}"
    return {
        "answer": texto,
        "items": [],
        "meta": {"intencion": "cortesia", "tipo": intencion, "requests_buk": 0},
    }


def responder_cumpleanos(plan):
    """Cumpleanos del rango. Si no hay ninguno, muestra los que vienen.

    Preguntar "quien esta de cumpleanos" y recibir "nadie" a secas no sirve de
    nada: lo util es saber a quien hay que saludar pronto.
    """
    desde, hasta = plan["desde"], plan["hasta"]
    hoy = date.today()
    dias_rango = max((hasta - desde).days, 0)
    gente, req = buk.cumpleanos(desde, dias_rango, hoy=hoy)

    if gente:
        if dias_rango == 0 and len(gente) == 1:
            texto = f"Hoy está de cumpleaños {gente[0]['nombre']}."
        elif dias_rango == 0:
            nombres = ", ".join(p["nombre"] for p in gente)
            texto = f"Hoy están de cumpleaños {len(gente)} personas: {nombres}."
        else:
            pendientes = [p for p in gente if p["faltan"] >= 0]
            texto = f"{len(gente)} cumpleaños {plan['etiqueta']}"
            if pendientes and len(pendientes) < len(gente):
                texto += f", {len(pendientes)} todavía por venir."
            else:
                texto += "."
        return {"answer": texto, "items": [_item_cumple(p) for p in gente],
                "meta": {"intencion": "cumpleanos", "requests_buk": req}}

    proximos, _ = buk.cumpleanos(hoy, settings.CUMPLE_HORIZONTE_DIAS, hoy=hoy)
    if not proximos:
        texto = (f"Nadie cumple años {plan['etiqueta']}, y tampoco en los "
                 f"próximos {settings.CUMPLE_HORIZONTE_DIAS} días.")
        return {"answer": texto, "items": [],
                "meta": {"intencion": "cumpleanos", "requests_buk": req}}

    faltan = proximos[0]["faltan"]
    cuando = "mañana" if faltan == 1 else f"en {faltan} días"
    siguientes = [p for p in proximos if p["faltan"] == faltan]
    if len(siguientes) == 1:
        quien = siguientes[0]["nombre"]
    else:
        quien = ", ".join(p["nombre"] for p in siguientes)
    texto = (f"Nadie cumple años {plan['etiqueta']}. "
             f"{'El próximo es' if len(siguientes) == 1 else 'Los próximos son'} "
             f"{cuando}: {quien}.")
    return {"answer": texto, "items": [_item_cumple(p) for p in proximos],
            "meta": {"intencion": "cumpleanos", "proximos": True, "requests_buk": req}}


def _item_cumple(persona):
    faltan = persona["faltan"]
    if faltan < 0:
        cuando = "ya pasó"
    elif faltan == 0:
        cuando = "hoy"
    elif faltan == 1:
        cuando = "mañana"
    else:
        cuando = f"en {faltan} días"
    return {
        "id": persona["id"],
        "nombre": persona.get("nombre_completo") or persona["nombre"],
        "cargo": " · ".join(x for x in (persona.get("cargo"), persona.get("area")) if x),
        "tipo": "cumpleaños",
        "detalle": "",
        "desde": persona["fecha"],
        "hasta": None,
        "dias": None,
        "estado": cuando,
        "media_jornada": False,
    }


def responder_trabajando(plan):
    """Quien SI esta en su jornada, con filtro por cuenta, area o cargo."""
    desde, hasta = plan["desde"], plan["hasta"]
    personas_map, req_dir = buk.directorio()
    aviso = _grupo_no_disponible(plan["mensaje"], personas_map, req_dir)
    if aviso:
        return aviso
    registros, req = buk.fuera(desde, hasta)

    equipo = list(personas_map.values())
    texto_plano = intents.normalizar(plan["mensaje"])
    donde = []

    cuenta = cuentas.buscar(plan["mensaje"])
    if cuenta and "ambiguas" in cuenta:
        return responder_cuenta_ambigua(cuenta["ambiguas"], plan["mensaje"])
    if cuenta:
        equipo = _filtrar_cuenta(equipo, cuenta["nombre"])
        donde.append(f"de {cuenta['nombre']}")
    else:
        area = intents.detectar_area(
            texto_plano, {p["area"] for p in personas_map.values() if p.get("area")})
        if area:
            equipo = _filtrar_area(equipo, area)
            donde.append(f"de {area}")

    # "que ejecutivos estan disponibles" filtra ademas por familia de cargo
    familia = intents.detectar_familia(texto_plano, _familias(personas_map))
    quienes = familia.lower() if familia else "personas"
    if familia:
        equipo = _filtrar_familia(equipo, familia)

    fuera_ids = {r["employee_id"] for r in registros}
    presentes = sorted((p for p in equipo if p["id"] not in fuera_ids),
                       key=lambda p: p["nombre"])
    total, ausentes = len(equipo), len(equipo) - len(presentes)
    grupo = " ".join([quienes] + donde)

    if not total:
        texto = f"No encontré {grupo} en la nómina."
    elif not ausentes:
        unidad = quienes if total > 1 else quienes.rstrip("es").rstrip("s")
        texto = (f"Están {'los' if total > 1 else 'el'} {total} {unidad} "
                 f"{' '.join(donde)} en su jornada {plan['etiqueta']}.".replace("  ", " "))
    else:
        verbo = "está" if ausentes == 1 else "están"
        texto = (f"{len(presentes)} de {total} {grupo} están en su jornada "
                 f"{plan['etiqueta']}; {ausentes} {verbo} fuera.")

    # Si el grupo es acotado se listan por nombre: preguntar "que ejecutivos
    # estan disponibles" y recibir solo un numero no responde la pregunta.
    items = []
    if 0 < len(presentes) <= settings.LISTAR_HASTA and (familia or cuenta or donde):
        items = [{
            "id": p["id"],
            "nombre": p.get("nombre_completo") or p["nombre"],
            "cargo": " · ".join(x for x in (p.get("cargo"), p.get("area")) if x),
            "tipo": "disponible", "detalle": "", "desde": None, "hasta": None,
            "dias": None, "estado": "en su jornada", "media_jornada": False,
        } for p in presentes]

    return {
        "answer": texto,
        "items": items,
        "meta": {"intencion": "trabajando", "presentes": len(presentes),
                 "ausentes": ausentes, "requests_buk": req + req_dir},
    }


def responder_pertenencia(plan):
    """Quien COMPONE un equipo, o si una persona pertenece a el.

    Es distinto de la disponibilidad: aca no importa si hoy esta o no, importa
    si la persona esta asignada a esa cuenta o area.
    """
    mensaje = plan["mensaje"]
    personas_map, req = buk.directorio()
    texto = intents.normalizar(mensaje)

    cuenta = cuentas.buscar(mensaje)
    if cuenta and "ambiguas" in cuenta:
        return responder_cuenta_ambigua(cuenta["ambiguas"], mensaje)

    area = None
    if not cuenta:
        area = intents.detectar_area(
            texto, {p["area"] for p in personas_map.values() if p.get("area")})

    if not cuenta and not area:
        aviso = _grupo_no_disponible(mensaje, personas_map, req)
        if aviso:
            return aviso
        return None   # no nombra ningun equipo: que siga el router

    grupo = cuenta["nombre"] if cuenta else area
    if cuenta:
        miembros = _filtrar_cuenta(list(personas_map.values()), grupo)
    else:
        miembros = _filtrar_area(list(personas_map.values()), grupo)
    miembros.sort(key=lambda p: p["nombre"])

    # "¿Taqui esta en el equipo de Santander?" se responde si o no, no con la
    # lista completa ni con su disponibilidad.
    ids, _ = personas.buscar(mensaje, personas_map)
    if len(ids) == 1:
        pid = next(iter(ids))
        persona = personas_map[pid]
        quien = persona.get("nombre_completo") or persona["nombre"]
        if any(m["id"] == pid for m in miembros):
            texto_r = f"Sí, {quien} está en {grupo}."
        else:
            suyas = persona.get("cuentas") or []
            texto_r = f"No, {quien} no está en {grupo}."
            if suyas:
                texto_r += (f" Está en {', '.join(suyas[:6])}"
                            + (" y otras." if len(suyas) > 6 else "."))
            elif persona.get("area"):
                texto_r += f" Aparece en el área de {persona['area']}."
        return {"answer": texto_r, "items": [],
                "meta": {"intencion": "pertenencia", "grupo": grupo,
                         "requests_buk": req}}
    if len(ids) > 1:
        return responder_ambiguo(ids, personas_map, mensaje, req)

    if not miembros:
        return {"answer": f"No hay nadie asignado a {grupo}.", "items": [],
                "meta": {"intencion": "pertenencia", "grupo": grupo,
                         "requests_buk": req}}

    texto_r = (f"{len(miembros)} "
               f"{'persona' if len(miembros) == 1 else 'personas'} en {grupo}.")
    items = [{
        "id": p["id"],
        "nombre": p.get("nombre_completo") or p["nombre"],
        "cargo": " · ".join(x for x in (p.get("cargo"), p.get("area")) if x),
        "tipo": "en el equipo", "detalle": "", "desde": None, "hasta": None,
        "dias": None, "estado": "", "media_jornada": False,
    } for p in miembros[: settings.LISTAR_HASTA]]
    if len(miembros) > settings.LISTAR_HASTA:
        texto_r += f" Te muestro {settings.LISTAR_HASTA}."
    return {"answer": texto_r, "items": items,
            "meta": {"intencion": "pertenencia", "grupo": grupo,
                     "requests_buk": req}}


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


def contexto_de_reglas(mensaje, hoy):
    """Lo que las reglas pueden traer sola, para adelantarselo al modelo.

    Le ahorra un viaje: sin esto el modelo pide los datos con una herramienta y
    recien en la segunda vuelta redacta, que es la mitad de la demora.
    """
    try:
        desde, hasta, etiqueta = intents.detectar_rango(intents.normalizar(mensaje), hoy)
        registros, _ = buk.fuera(desde, hasta)
        personas_map, _ = buk.directorio()
    except buk.BukError:
        return None

    # Solo lo indispensable: el contexto viaja en cada llamada y cada campo de
    # mas se paga en latencia.
    def compacto(r):
        persona = personas_map.get(r["employee_id"]) or {}
        cfg = buk.CATEGORIAS.get(r["categoria"], {})
        return {
            "nombre": persona.get("nombre") or f"Empleado #{r['employee_id']}",
            "cargo": persona.get("cargo") or "",
            "tipo": cfg.get("etiqueta", r["categoria"]),
            "desde": r["start_date"],
            "hasta": r["end_date"],
        }

    return {
        "hoy": hoy.isoformat(),
        "rango": f"{desde.isoformat()} a {hasta.isoformat()} ({etiqueta})",
        "personas_activas": len(personas_map),
        "fuera_de_jornada": [compacto(r) for r in registros],
    }


def responder_con_modelo(mensaje, contexto=None):
    """Respaldo con modelo de lenguaje. None si no hay clave o si no resolvio."""
    if not asistente.disponible():
        return None
    try:
        texto, meta = asistente.responder(mensaje, date.today(), contexto)
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
            "con_contexto": bool(contexto),
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
    # Lo llama la pagina al cargar: recargar empieza una conversacion limpia,
    # sin quedar enganchado al ultimo cliente consultado.
    request.session.pop("grupo", None)
    request.session.pop("pendiente", None)

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


# Respuestas donde tiene sentido acordarse de un equipo o heredarlo.
INTENCIONES_CON_GRUPO = ("trabajando", "ausencias", "pertenencia", "cumpleanos")


def _grupo_mencionado(mensaje, personas_map):
    """Cuenta o area nombrada en el mensaje, para recordarla o heredarla."""
    cuenta = cuentas.buscar(mensaje)
    if cuenta and "ambiguas" not in cuenta:
        return {"tipo": "cuenta", "nombre": cuenta["nombre"]}
    area = intents.detectar_area(
        intents.normalizar(mensaje),
        {p["area"] for p in personas_map.values() if p.get("area")})
    return {"tipo": "area", "nombre": area} if area else None


# Palabras con las que el usuario sale explicitamente de un equipo.
SALIR_DEL_GRUPO = ("en general", "en total", "de todos", "todos", "todas",
                   "toda la empresa", "de la empresa", "en la empresa",
                   "en toda", "global", "completo", "cualquiera")

# Pronombres que abren una pregunta nueva y completa.
PREGUNTA_PROPIA = ("quien", "quienes", "cuanto", "cuanta", "cuantos", "cuantas",
                   "cual", "cuales", "que ", "donde", "cuando")

MAX_PALABRAS_CONTINUACION = 5


def _es_continuacion(mensaje):
    """True si el mensaje solo se entiende con la pregunta anterior.

    "estan disponibles" no dice de quien, asi que hereda. "necesito saber quien
    esta de vacaciones" es una pregunta completa: heredar ahi es justamente lo
    que hacia que la conversacion se quedara pegada a un cliente.
    """
    texto = intents.normalizar(mensaje).strip()
    if any(p in texto for p in SALIR_DEL_GRUPO):
        return False
    if any(p in f"{texto} " for p in PREGUNTA_PROPIA):
        return False
    return 0 < len(texto.split()) <= MAX_PALABRAS_CONTINUACION


def _heredar_grupo(mensaje, contexto, personas_map):
    """Agrega al mensaje el equipo del que se venia hablando.

    Solo para fragmentos que no se entienden solos, y nunca si el mensaje
    nombra otro grupo o pide explicitamente todo.
    """
    if not contexto or not _es_continuacion(mensaje):
        return mensaje, None
    if _grupo_mencionado(mensaje, personas_map):
        return mensaje, None
    return f"{mensaje} en {contexto['nombre']}", contexto


def _sin_privados(respuesta):
    """Quita las claves internas antes de mandar la respuesta al navegador."""
    return {k: v for k, v in respuesta.items() if not k.startswith("_")}


def resolver_pendiente(mensaje, pendiente, hoy):
    """Interpreta el mensaje como respuesta a un "¿cuál de estas?" anterior.

    Si se resuelve, se responde la pregunta ORIGINAL con la opcion elegida: el
    usuario pregunto por las vacaciones de la Javi, no por el numero 2.
    """
    opciones = pendiente.get("opciones") or []
    indice = elegir_opcion(mensaje, opciones)
    if indice is None:
        return None

    pregunta = pendiente.get("pregunta") or ""
    if pendiente.get("tipo") == "persona":
        personas_map, req = buk.directorio()
        pid = (pendiente.get("ids") or [None] * len(opciones))[indice]
        if pid not in personas_map:
            return None
        plan = intents.interpretar(pregunta, hoy)
        plan["mensaje"] = pregunta
        plan.setdefault("desde", hoy)
        plan.setdefault("hasta", hoy)
        plan.setdefault("etiqueta", "hoy")
        respuesta = responder_persona(plan, {pid}, personas_map)
        respuesta["meta"]["requests_buk"] += req
        respuesta["meta"]["desambiguado"] = True
        return respuesta

    if pendiente.get("tipo") == "cuenta":
        # se rehace la pregunta original con el nombre completo de la cuenta
        elegida = opciones[indice]
        respuesta = _resolver(f"{pregunta} ({elegida})", hoy)
        respuesta["meta"]["desambiguado"] = True
        return respuesta

    return None


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

    # ¿Es la respuesta a un "¿cual de estas?" que quedo pendiente?
    pendiente = request.session.get("pendiente")
    if pendiente:
        del request.session["pendiente"]
        try:
            resuelta = resolver_pendiente(mensaje, pendiente, hoy)
        except buk.BukError as error:
            return JsonResponse({"error": str(error)}, status=502)
        if resuelta:
            contar(mensaje, resuelta["meta"].get("intencion"))
            return JsonResponse(_sin_privados(resuelta))

    # Una pregunta corta que sigue a otra hereda el equipo: "estan disponible"
    # despues de "quienes estan en Cencosud" habla de Cencosud.
    contexto_grupo = request.session.get("grupo")
    heredado = None
    if (contexto_grupo
            and intents.interpretar(mensaje, hoy)["intencion"] in INTENCIONES_CON_GRUPO):
        try:
            personas_map, _ = buk.directorio()
            mensaje_efectivo, heredado = _heredar_grupo(
                mensaje, contexto_grupo, personas_map)
        except buk.BukError:
            mensaje_efectivo = mensaje
        if heredado:
            mensaje = mensaje_efectivo

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
    # Una pregunta que quedo esperando aclaracion no se cachea: la respuesta
    # depende de lo que conteste el usuario, no solo del texto.
    # Se recuerda el equipo nombrado, pero solo cuando la respuesta fue sobre
    # personas: un saludo o una politica no tienen por que consultar BUK.
    if any(p in intents.normalizar(mensaje) for p in SALIR_DEL_GRUPO):
        request.session.pop("grupo", None)      # "en general" olvida el equipo
    elif respuesta["meta"].get("intencion") in INTENCIONES_CON_GRUPO:
        try:
            personas_map, _ = buk.directorio()
            grupo = _grupo_mencionado(mensaje, personas_map)
            if grupo:
                request.session["grupo"] = grupo
                request.session.set_expiry(settings.DESAMBIGUACION_SEGUNDOS)
        except buk.BukError:
            pass

    if heredado:
        respuesta["meta"]["grupo_heredado"] = heredado["nombre"]

    pendiente_nuevo = respuesta.pop("_pendiente", None)
    if pendiente_nuevo is not None:
        request.session["pendiente"] = pendiente_nuevo
        request.session.set_expiry(settings.DESAMBIGUACION_SEGUNDOS)
    else:
        respuestas.guardar(mensaje, hoy, respuesta)
    contar(mensaje, respuesta["meta"].get("intencion"))
    return JsonResponse(respuesta)


def _responder_si_nombra_persona(plan, hoy):
    """Respuesta sobre la persona nombrada, o None si no nombra a ninguna.

    Va antes de las vistas de grupo. Un nombre es mucho mas especifico que el
    verbo de la pregunta: quien escribe "felipe toro esta disponible?" quiere
    saber de Felipe, no un resumen de la empresa.
    """
    mensaje = plan["mensaje"]
    try:
        personas_map, req_dir = buk.directorio()
        ids, _ = personas.buscar(mensaje, personas_map)
    except buk.BukError:
        return None   # sin BUK todavia se puede responder desde documentos

    if not ids:
        # Nadie coincide, pero puede ser un nombre mal escrito. Se revisa aca y
        # no solo al final: "rojs esta disponible?" se iria a la vista de grupo
        # y contestaria por las 98 personas.
        sugeridos = personas.sugerir(mensaje, personas_map)
        if len(sugeridos) == 1:
            return responder_quiso_decir(sugeridos[0], personas_map, mensaje, req_dir)
        if sugeridos:
            return responder_ambiguo(set(sugeridos), personas_map, mensaje, req_dir)
        return None
    if len(ids) > 1:
        # Puede ser un apodo repetido (hay cinco "Javi") o un apellido mal
        # escrito que dejo solo el nombre de pila coincidiendo. La sugerencia
        # se cruza con lo ya encontrado en vez de reemplazarlo: "felipe garrid"
        # tiene que quedarse con el Felipe apellidado Garrido, no con la otra
        # persona de ese apellido.
        sugeridos = set(personas.sugerir(mensaje, personas_map))
        refinado = sugeridos & ids
        if len(refinado) == 1:
            return responder_quiso_decir(next(iter(refinado)), personas_map,
                                         mensaje, req_dir)
        if len(sugeridos) == 1:
            return responder_quiso_decir(next(iter(sugeridos)), personas_map,
                                         mensaje, req_dir)
        return responder_ambiguo(ids, personas_map, mensaje, req_dir)

    plan_persona = {
        "desde": plan.get("desde") or hoy,
        "hasta": plan.get("hasta") or plan.get("desde") or hoy,
        "etiqueta": plan.get("etiqueta") or "hoy",
        "categoria": plan.get("categoria"),
        "subtipo": plan.get("subtipo"),
        "mensaje": mensaje,
    }
    respuesta = responder_persona(plan_persona, ids, personas_map)
    respuesta["meta"]["requests_buk"] += req_dir
    return respuesta


def responder_quiso_decir(pid, personas_map, mensaje, req):
    """Sugiere el nombre parecido en vez de responder por quien no se pidio."""
    persona = personas_map[pid]
    nombre = persona.get("nombre_completo") or persona["nombre"]
    registrar(mensaje, "persona_desconocida")
    return {
        "answer": f"No encontré ese nombre exacto. ¿Querrás decir {nombre}?",
        "items": [],
        "meta": {"intencion": "quiso_decir", "requests_buk": req},
        "_pendiente": {"tipo": "persona", "ids": [pid], "opciones": [nombre],
                       "pregunta": mensaje},
    }


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

        # Las reglas arman su mejor respuesta ANTES de llamar al modelo. Sirve
        # de dos maneras: se le pasa como contexto (una vuelta menos) y queda
        # como red si el modelo falla o se demora.
        respaldo, contexto = None, None
        if asistente.disponible():
            contexto = contexto_de_reglas(mensaje, hoy)
        try:
            plan_base = intents.interpretar(mensaje, hoy)
            plan_base["mensaje"] = mensaje
            if plan_base["intencion"] == "ausencias":
                respaldo = responder_ausencias(plan_base)
        except buk.BukError:
            respaldo = None

        del_modelo = responder_con_modelo(mensaje, contexto)
        if del_modelo:
            return del_modelo
        if respaldo:
            respaldo["meta"]["intencion"] = "reglas_respaldo"
            respaldo["meta"]["parcial"] = True
            respaldo["answer"] = (
                f"{respaldo['answer']} No pude afinar más la respuesta en este "
                "momento, así que te dejo el detalle completo para que lo revises."
            )
            return respaldo
        return responder_sin_datos(mensaje)

    plan = intents.interpretar(mensaje)
    plan["mensaje"] = mensaje

    # "como pido vacaciones" menciona vacaciones pero pregunta por el
    # procedimiento: los documentos responden antes que BUK.
    if intents.es_procedimiento(mensaje):
        seccion = documentos.responder(mensaje)
        if seccion:
            return responder_documento(seccion)

    # La cortesia va primero: un "hola" no tiene por que gastar una consulta al
    # directorio buscando a alguien que no se nombro.
    if plan["intencion"] in CORTESIA:
        return responder_cortesia(plan["intencion"], mensaje)

    # "¿quienes estan en el equipo de X?" pregunta por la composicion, no por
    # la disponibilidad. Va antes de la busqueda de persona porque "¿Taqui esta
    # en el equipo de Santander?" nombra a alguien pero no pregunta si esta hoy.
    if plan["intencion"] == "pertenencia":
        respuesta = responder_pertenencia(plan)
        if respuesta is not None:
            return respuesta

    # Si la pregunta nombra a alguien, esa persona manda sobre la intencion de
    # grupo: "felipe toro esta disponible?" pregunta por Felipe, no por la
    # nomina entera. Antes el "disponible" se llevaba la pregunta y contestaba
    # por las 98 personas, ignorando el nombre.
    respuesta_persona = _responder_si_nombra_persona(plan, hoy)
    if respuesta_persona is not None:
        return respuesta_persona

    if plan["intencion"] == "ausencias":
        return responder_ausencias(plan)
    if plan["intencion"] == "dotacion":
        return responder_dotacion()
    if plan["intencion"] == "cumpleanos":
        return responder_cumpleanos(plan)
    if plan["intencion"] == "trabajando":
        return responder_trabajando(plan)

    seccion = documentos.responder(mensaje)
    if seccion:
        return responder_documento(seccion)

    contexto = contexto_de_reglas(mensaje, hoy) if asistente.disponible() else None
    del_modelo = responder_con_modelo(mensaje, contexto)
    if del_modelo:
        return del_modelo

    return responder_sin_datos(mensaje)
