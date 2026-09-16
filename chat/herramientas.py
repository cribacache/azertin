"""Las funciones que el modelo puede llamar.

El modelo nunca recibe la clave de BUK ni el payload crudo: solo puede invocar
estas funciones, que reutilizan la misma capa sanitizada que usa el router de
reglas. Lo que no pase por aca, el modelo no lo puede ver ni inventar.
"""

from datetime import date, timedelta

from . import buk, cuentas, documentos, intents, personas, turnos

MAX_PERSONAS = 60  # techo para no mandar listados enormes al modelo


def _fecha(valor, por_defecto=None):
    try:
        return date.fromisoformat(valor)
    except (TypeError, ValueError):
        return por_defecto or date.today()


def _persona_publica(registro, directorio):
    persona = directorio.get(registro["employee_id"]) or {}
    cfg = buk.CATEGORIAS.get(registro["categoria"], {})
    # "detalle" trae el subtipo real cuando BUK lo distingue (vacaciones:
    # feriado legal, dia administrativo, vacacion progresiva, dia adicional).
    # Sin esto, alguien con dia administrativo aparecia como "vacaciones" a
    # secas: mucho mas generico de lo que Iris ya sabia. Licencia/permiso/
    # inasistencia no tienen ese detalle (BUK no lo distingue mas alla del
    # tipo), asi que ahi se sigue mostrando la etiqueta de la categoria.
    tipo = registro.get("detalle") or cfg.get("etiqueta", registro["categoria"])
    return {
        "nombre": persona.get("nombre") or f"Empleado #{registro['employee_id']}",
        "cargo": persona.get("cargo") or "",
        "tipo": tipo,
        "desde": registro["start_date"],
        "hasta": registro["end_date"],
        "media_jornada": bool(registro.get("media_jornada")),
    }


def listar_ausencias(desde=None, hasta=None, categoria=None):
    """Quien no esta en su jornada dentro de un rango."""
    d1 = _fecha(desde)
    d2 = _fecha(hasta, d1)
    if categoria not in buk.CATEGORIAS:
        categoria = None

    registros, _ = buk.fuera(d1, d2, categoria)
    directorio, _ = buk.directorio()
    personas_out = [_persona_publica(r, directorio) for r in registros]
    personas_out.sort(key=lambda p: (p["desde"] or "", p["nombre"]))

    return {
        "rango": {"desde": d1.isoformat(), "hasta": d2.isoformat()},
        "categoria": categoria or "todas",
        "total": len(personas_out),
        "personas": personas_out[:MAX_PERSONAS],
        "truncado": len(personas_out) > MAX_PERSONAS,
    }


def ausencias_de_persona(nombre, desde=None, hasta=None):
    """Situacion de una persona concreta. Resuelve el nombre localmente."""
    directorio, _ = buk.directorio()
    ids, _ = personas.buscar(nombre or "", directorio)

    if not ids:
        return {"encontrada": False, "motivo": "No hay nadie con ese nombre en la nomina activa."}
    if len(ids) > 1:
        return {
            "encontrada": False,
            "motivo": "El nombre coincide con varias personas.",
            "candidatos": sorted(directorio[i]["nombre"] for i in ids)[:8],
        }

    pid = next(iter(ids))
    d1 = _fecha(desde)
    d2 = _fecha(hasta, d1)
    registros, _ = buk.fuera(d1, d2)
    suyos = [r for r in registros if r["employee_id"] == pid]

    return {
        "encontrada": True,
        "nombre": directorio[pid]["nombre"],
        "cargo": directorio[pid]["cargo"],
        "rango": {"desde": d1.isoformat(), "hasta": d2.isoformat()},
        "ausencias": [_persona_publica(r, directorio) for r in suyos],
    }


def estado_solicitudes(nombre, desde=None, hasta=None):
    """En que estado esta(n) la(s) solicitud(es) de vacaciones/dia
    administrativo/licencia/permiso de una persona (aprobada, pendiente,
    rechazada).

    A diferencia de ausencias_de_persona (que dice si alguien esta o va a
    estar fuera, y para eso descarta las rechazadas y no distingue estado),
    esta trae CUALQUIER solicitud que se solape con el rango -aprobada,
    pendiente o rechazada- con su estado explicito. Es la que responde "en
    que va mi permiso/dia administrativo del 20 de octubre", no si esa
    persona va a estar presente ese dia.
    """
    directorio, _ = buk.directorio()
    ids, _ = personas.buscar(nombre or "", directorio)

    if not ids:
        return {"encontrada": False, "motivo": "No hay nadie con ese nombre en la nomina activa."}
    if len(ids) > 1:
        return {
            "encontrada": False,
            "motivo": "El nombre coincide con varias personas.",
            "candidatos": sorted(directorio[i]["nombre"] for i in ids)[:8],
        }

    pid = next(iter(ids))
    hoy = date.today()
    # Sin fechas: ventana amplia (solicitudes recientes + las que vienen). Con
    # solo "desde" (una fecha puntual por la que preguntan): un solo dia, para
    # encontrar la solicitud que la cubre, igual que ausencias_de_persona.
    d1 = _fecha(desde, hoy - timedelta(days=60))
    d2 = _fecha(hasta, d1 if desde else hoy + timedelta(days=180))

    vac, _ = buk.vacaciones(d1, d2, incluir_todas=True)
    aus, _ = buk.ausencias(d1, d2, incluir_todas=True)
    suyas = sorted(
        (r for r in vac + aus if r["employee_id"] == pid),
        key=lambda r: r["start_date"] or "",
    )

    return {
        "encontrada": True,
        "nombre": directorio[pid]["nombre"],
        "rango": {"desde": d1.isoformat(), "hasta": d2.isoformat()},
        "total": len(suyas),
        "solicitudes": [
            {
                "tipo": r["detalle"] or buk.CATEGORIAS.get(r["categoria"], {}).get(
                    "etiqueta", r["categoria"]),
                "desde": r["start_date"],
                "hasta": r["end_date"],
                "estado": r["estado"],
                "solicitado": r["solicitado"],
            }
            for r in suyas[:MAX_PERSONAS]
        ],
    }


def info_persona(nombre):
    """Quien es una persona: cargo, area, correo corporativo y cuentas/clientes
    que atiende.

    Cubre "quien es X", "que cuentas maneja X", "cual es el correo de X": son
    variantes de la misma pregunta, y el modelo decide cuando usarla en vez de
    tener que programar cada forma de decirla.
    """
    directorio, _ = buk.directorio()
    ids, _ = personas.buscar(nombre or "", directorio)

    if not ids:
        return {"encontrada": False, "motivo": "No hay nadie con ese nombre en la nomina activa."}
    if len(ids) > 1:
        return {
            "encontrada": False,
            "motivo": "El nombre coincide con varias personas.",
            "candidatos": sorted(directorio[i]["nombre"] for i in ids)[:8],
        }

    pid = next(iter(ids))
    persona = directorio[pid]
    return {
        "encontrada": True,
        "nombre": persona["nombre"],
        "cargo": persona.get("cargo") or "",
        "area": persona.get("area") or "",
        "email": persona.get("email") or "",
        "cuentas": persona.get("cuentas") or [],
    }


def equipo_de(grupo):
    """Quien compone un equipo: una cuenta/cliente o un area.

    Cubre "quien es del equipo/cuenta de Y" y "muestrame el equipo que
    atiende Y": es la pregunta inversa a `info_persona` (ahi se pregunta por
    la persona, aca por el grupo).
    """
    directorio, _ = buk.directorio()
    cuenta = cuentas.buscar(grupo or "")

    if cuenta and "ambiguas" in cuenta:
        return {
            "encontrado": False,
            "motivo": "El nombre coincide con varias cuentas.",
            "candidatos": cuenta["ambiguas"],
        }

    if cuenta:
        nombre_grupo = cuenta["nombre"]
        miembros = [p for p in directorio.values()
                    if nombre_grupo in (p.get("cuentas") or [])]
    else:
        nombres_area = {p["area"] for p in directorio.values() if p.get("area")}
        area = intents.detectar_area(intents.normalizar(grupo or ""), nombres_area)
        if not area:
            return {"encontrado": False,
                    "motivo": "No encuentro esa cuenta ni esa area."}
        nombre_grupo = area
        miembros = [p for p in directorio.values() if p.get("area") == area]

    miembros.sort(key=lambda p: p["nombre"])
    return {
        "encontrado": True,
        "grupo": nombre_grupo,
        "total": len(miembros),
        "personas": [{"nombre": p["nombre"], "cargo": p.get("cargo") or ""}
                     for p in miembros[:MAX_PERSONAS]],
    }


def persona_por_cargo(cargo):
    """Quien ocupa un cargo, cuando la pregunta no nombra a nadie.

    Cubre "quien es el gerente de personas": no hay un nombre que resolver,
    hay que buscar por el texto del cargo en el directorio.
    """
    from .intents import normalizar

    directorio, _ = buk.directorio()
    palabras = {p for p in normalizar(cargo or "").split() if len(p) >= 3}
    if not palabras:
        return {"encontrada": False, "motivo": "No especificaste un cargo."}

    coincidencias = [
        p for p in directorio.values()
        if palabras <= set(normalizar(p.get("cargo") or "").split())
    ]
    if not coincidencias:
        return {"encontrada": False, "motivo": "Nadie tiene registrado ese cargo en BUK."}
    if len(coincidencias) > 1:
        return {
            "encontrada": False,
            "motivo": "Varias personas tienen un cargo parecido.",
            "candidatos": sorted(p["nombre"] for p in coincidencias)[:8],
        }

    persona = coincidencias[0]
    return {
        "encontrada": True,
        "nombre": persona["nombre"],
        "cargo": persona.get("cargo") or "",
        "area": persona.get("area") or "",
        "cuentas": persona.get("cuentas") or [],
    }


_ULTIMO_DIA_MES = {1: 31, 2: 28, 3: 31, 4: 30, 5: 31, 6: 30,
                   7: 31, 8: 30, 9: 30, 10: 31, 11: 30, 12: 31}


def _rango_calendario(rango, hoy):
    """Limites de "hoy"/"esta_semana"/"este_mes" como (desde, dias).

    Calcularlos aca, no dejar que el modelo adivine: una semana es de lunes a
    domingo, no "7 dias desde hoy", y un mes es del 1 al ultimo dia de ESE
    mes, no "30 dias desde hoy" (que en la mayoria de los meses se pasa para
    el mes siguiente, o se queda corto).
    """
    if rango == "esta_semana":
        lunes = hoy - timedelta(days=hoy.weekday())
        return lunes, 6
    if rango == "este_mes":
        primero = hoy.replace(day=1)
        ultimo_dia = _ULTIMO_DIA_MES[hoy.month]
        if hoy.month == 2 and hoy.year % 4 == 0 and (hoy.year % 100 != 0 or hoy.year % 400 == 0):
            ultimo_dia = 29  # bisiesto
        ultimo = hoy.replace(day=ultimo_dia)
        return primero, (ultimo - primero).days
    return hoy, 0  # "hoy" o cualquier otro valor: solo el dia de hoy


def cumpleanos(desde=None, dias=0, rango=None):
    """Quien cumple anos en un rango. Solo dia y mes: el anio no se expone.

    `rango` ("hoy" / "esta_semana" / "este_mes") gana sobre `desde`/`dias`
    cuando viene: son los pedidos de calendario exacto. `desde`/`dias` quedan
    para un rango a medida (por ejemplo, ampliado por `cumpleanos_de_persona`
    para encontrar a alguien puntual).
    """
    hoy = date.today()
    if rango:
        d, dias_calc = _rango_calendario(rango, hoy)
    else:
        d, dias_calc = _fecha(desde, hoy), max(int(dias or 0), 0)

    gente, _ = buk.cumpleanos(d, dias_calc, hoy=hoy)
    return {
        "desde": d.isoformat(),
        "hasta": (d + timedelta(days=dias_calc)).isoformat(),
        "total": len(gente),
        "personas": [{"nombre": p["nombre"], "cargo": p["cargo"], "area": p["area"],
                      "fecha": p["fecha"], "faltan_dias": p["faltan"]}
                     for p in gente[:MAX_PERSONAS]],
    }


def cumpleanos_de_persona(nombre):
    """Cuando cumple anos UNA persona. Resuelve el nombre localmente.

    Sin esto, encontrar el cumpleanos de alguien puntual significaba pedirle
    a `cumpleanos` un rango de 366 dias y buscar el nombre en la respuesta -
    y esa respuesta viene recortada a MAX_PERSONAS (60): con casi 100
    personas activas, cualquiera cuyo cumpleanos cayera despues del puesto 60
    (ordenado por cercania) quedaba fuera del recorte, y el modelo terminaba
    diciendo que no sabia sobre un dato que BUK si tiene.
    """
    directorio, _ = buk.directorio()
    ids, _ = personas.buscar(nombre or "", directorio)

    if not ids:
        return {"encontrada": False, "motivo": "No hay nadie con ese nombre en la nomina activa."}
    if len(ids) > 1:
        return {
            "encontrada": False,
            "motivo": "El nombre coincide con varias personas.",
            "candidatos": sorted(directorio[i]["nombre"] for i in ids)[:8],
        }

    pid = next(iter(ids))
    hoy = date.today()
    # 366 dias, sin el recorte de `cumpleanos()`: acá se busca a una sola
    # persona conocida, no una lista para mostrar, así que no hay riesgo de
    # devolver una respuesta enorme.
    gente, _ = buk.cumpleanos(hoy, 366, hoy=hoy)
    encontrada = next((p for p in gente if p["id"] == pid), None)
    if encontrada is None:
        return {"encontrada": True, "nombre": directorio[pid]["nombre"],
                "fecha_conocida": False,
                "motivo": "No tengo esa fecha registrada en BUK."}

    return {
        "encontrada": True,
        "nombre": encontrada["nombre"],
        "fecha_conocida": True,
        "fecha": encontrada["fecha"],
        "faltan_dias": encontrada["faltan"],
    }


def dotacion():
    """Cuantas personas activas hay."""
    directorio, _ = buk.directorio()
    return {"personas_activas": len(directorio)}


def quien_esta_trabajando(desde=None, hasta=None, grupo=None):
    """Quien SI esta en su jornada (lo contrario de listar_ausencias).

    Sin esta herramienta el modelo tendria que restar `listar_ausencias` de
    `dotacion` el mismo, pero `dotacion` solo da un numero, no nombres: no
    hay como enumerar a quien SI esta sin esto. Acepta el mismo filtro de
    grupo que `equipo_de`, para "quien esta trabajando hoy en X".
    """
    d1 = _fecha(desde)
    d2 = _fecha(hasta, d1)
    directorio, _ = buk.directorio()

    nombre_grupo, base = None, directorio
    if grupo:
        cuenta = cuentas.buscar(grupo)
        if cuenta and "ambiguas" in cuenta:
            return {"encontrado": False, "motivo": "El nombre coincide con varias cuentas.",
                    "candidatos": cuenta["ambiguas"]}
        if cuenta:
            nombre_grupo = cuenta["nombre"]
            base = {pid: p for pid, p in directorio.items()
                    if nombre_grupo in (p.get("cuentas") or [])}
        else:
            nombres_area = {p["area"] for p in directorio.values() if p.get("area")}
            area = intents.detectar_area(intents.normalizar(grupo), nombres_area)
            if not area:
                return {"encontrado": False,
                        "motivo": "No encuentro esa cuenta ni esa area."}
            nombre_grupo = area
            base = {pid: p for pid, p in directorio.items() if p.get("area") == area}

    registros, _ = buk.fuera(d1, d2)
    ausentes_ids = {r["employee_id"] for r in registros if r["employee_id"] in base}
    presentes = sorted((p for pid, p in base.items() if pid not in ausentes_ids),
                       key=lambda p: p["nombre"])

    return {
        "encontrado": True,
        "grupo": nombre_grupo,
        "rango": {"desde": d1.isoformat(), "hasta": d2.isoformat()},
        "total": len(base),
        "trabajando": len(presentes),
        "fuera": len(base) - len(presentes),
        "personas": [{"nombre": p["nombre"], "cargo": p.get("cargo") or ""}
                     for p in presentes[:MAX_PERSONAS]],
    }


def listar_cuentas():
    """Nombres de todas las cuentas/clientes que administra Azerta."""
    nombres = cuentas.nombres()
    return {"total": len(nombres), "cuentas": nombres}


def listar_beneficios():
    """Beneficios que existen en Azerta (modulo Beneficios de BUK).

    En la practica es "beneficios que alguien ya solicito alguna vez": la API
    de BUK no tiene forma de listar el catalogo completo, solo de consultar
    las solicitudes reales y el detalle de cada una por su id.
    """
    solicitudes, _ = buk.beneficios()
    nombres = sorted({s["beneficio"] for s in solicitudes})
    return {"total": len(nombres), "beneficios": nombres}


def beneficios_de_persona(nombre):
    """Que beneficios ha solicitado una persona, y en que estado esta cada
    solicitud. Resuelve el nombre localmente, igual que ausencias_de_persona.
    """
    directorio, _ = buk.directorio()
    ids, _ = personas.buscar(nombre or "", directorio)

    if not ids:
        return {"encontrada": False, "motivo": "No hay nadie con ese nombre en la nomina activa."}
    if len(ids) > 1:
        return {
            "encontrada": False,
            "motivo": "El nombre coincide con varias personas.",
            "candidatos": sorted(directorio[i]["nombre"] for i in ids)[:8],
        }

    pid = next(iter(ids))
    solicitudes, _ = buk.beneficios()
    suyas = [s for s in solicitudes if s["employee_id"] == pid]
    return {
        "encontrada": True,
        "nombre": directorio[pid]["nombre"],
        "total": len(suyas),
        "beneficios": [{"beneficio": s["beneficio"], "estado": s["estado"],
                        "solicitado": s["solicitado"]} for s in suyas[:MAX_PERSONAS]],
    }


def turno_de_persona(nombre, fecha=None):
    """Turno, modalidad, puesto y si tiene presencial en una semana puntual,
    desde la planilla de turnos.

    BUK no tiene este dato: vive aparte (chat/turnos.py). Resuelve el nombre
    contra la nomina de BUK igual que el resto de las herramientas
    "de_persona", y despues cruza por nombre con esa planilla.

    "modalidad" ("Presencial"/"Hibrido") es la condicion general, pero para
    alguien hibrido con turno rotativo (Turno 1/Turno 2) no dice si ESTA
    semana puntual le toca presencial o no -eso alterna semana por medio,
    segun Hoja 2 (turnos.info_presencial). El campo "presencial" trae esa
    respuesta ya resuelta.
    """
    directorio, _ = buk.directorio()
    ids, _ = personas.buscar(nombre or "", directorio)

    if not ids:
        return {"encontrada": False, "motivo": "No hay nadie con ese nombre en la nomina activa."}
    if len(ids) > 1:
        return {
            "encontrada": False,
            "motivo": "El nombre coincide con varias personas.",
            "candidatos": sorted(directorio[i]["nombre"] for i in ids)[:8],
        }

    persona = directorio[next(iter(ids))]
    fila = turnos.buscar(persona["nombre"])
    if fila is None:
        return {
            "encontrada": True,
            "nombre": persona["nombre"],
            "turno_registrado": False,
            "motivo": "Esta persona no aparece en la planilla de turnos.",
        }
    return {
        "encontrada": True,
        "turno_registrado": True,
        "nombre": persona["nombre"],
        "area": fila["area"] or None,
        "forma_trabajo": fila["forma_trabajo"] or None,
        "modalidad": fila["modalidad"] or None,
        "puesto": fila["puesto"] if fila["puesto"] not in ("", "-") else None,
        "observacion": fila["observacion"] or None,
        "presencial": turnos.info_presencial(fila, _fecha(fecha)),
    }


def listar_turnos(modalidad=None, forma_trabajo=None, area=None):
    """Personas que matchean un turno/modalidad/area, sin nombrar a nadie.

    Al reves de turno_de_persona: aca se pregunta por el grupo, no por
    alguien puntual. Mismos filtros que ofrece la planilla (chat/turnos.py).
    """
    filas = turnos.listar(modalidad=modalidad, forma_trabajo=forma_trabajo, area=area)
    return {
        "total": len(filas),
        "personas": [
            {"nombre": f["nombre"], "cargo": f["cargo"], "area": f["area"],
             "forma_trabajo": f["forma_trabajo"], "modalidad": f["modalidad"]}
            for f in sorted(filas, key=lambda f: f["nombre"])
        ],
    }


def _correo_de(contexto):
    usuario = getattr(contexto, "usuario", None)
    return (getattr(usuario, "email", "") or "").strip()


def salas_disponibles(fecha, hora_inicio, hora_fin, _contexto=None):
    """Que salas de reuniones estan libres u ocupadas en un rango horario.

    Se consulta "actuando como" quien pregunta (necesita su correo real, no
    algo que el modelo pueda inventar): por eso recibe `_contexto`, inyectado
    por chat/asistente.py, y no forma parte del esquema que ve Gemini.
    """
    from . import salas

    correo = _correo_de(_contexto)
    if not correo:
        return {"error": "No pude identificar tu cuenta para consultar Calendar."}
    try:
        return {"salas": salas.disponibilidad(correo, fecha, hora_inicio, hora_fin)}
    except salas.SalasError as error:
        return {"error": str(error)}


def crear_reunion(sala, fecha, hora_inicio, hora_fin, titulo, invitados=None, _contexto=None):
    """Reserva una sala y crea el evento en Google Calendar, organizado por
    quien pregunta (ver salas_disponibles sobre `_contexto`)."""
    from . import salas as salas_mod

    correo = _correo_de(_contexto)
    if not correo:
        return {"creada": False, "motivo": "No pude identificar tu cuenta para crear la reunión."}
    try:
        return salas_mod.crear_reunion(correo, sala, fecha, hora_inicio, hora_fin, titulo,
                                       invitados)
    except salas_mod.SalasError as error:
        return {"creada": False, "motivo": str(error)}


# Las dos herramientas de reserva de salas, para poder apagarlas juntas
# (settings.SALAS_REUNIONES_HABILITADO, ver chat/asistente.py::salas_habilitadas)
# sin tocar el resto.
HERRAMIENTAS_SALAS = {"salas_disponibles", "crear_reunion"}

# Herramientas que necesitan saber quien pregunta (su correo real), no solo
# los argumentos que arma el modelo: chat/asistente.py les inyecta
# `_contexto` antes de llamarlas, fuera del esquema que ve Gemini.
NECESITAN_CONTEXTO = HERRAMIENTAS_SALAS

# Estas dos hablan con Calendar en tiempo real (disponibilidad que cambia
# minuto a minuto) o tienen efecto de lado (crean un evento real): cachear su
# respuesta como cualquier otra pregunta serviria una disponibilidad vieja o
# escondería que ya se puede volver a intentar. Ver chat/respuestas.py.
NO_CACHEABLES = HERRAMIENTAS_SALAS


def buscar_politica(consulta):
    """Busca en los documentos internos (politicas, procedimientos)."""
    from .antiprompt import NOTA_DOCUMENTO

    secciones = documentos.buscar(consulta or "", cuantas=3)
    if not secciones:
        return {"encontrada": False}
    return {
        "encontrada": True,
        # El texto de un documento es material de referencia, no ordenes para
        # el modelo: se rotula para que no confunda una frase imperativa del
        # reglamento con una instruccion dirigida a el.
        "nota": NOTA_DOCUMENTO,
        "secciones": [
            {"titulo": s["titulo"], "contenido": s["cuerpo"], "fuente": s["origen"]}
            for s in secciones
        ],
    }


FUNCIONES = {
    "listar_ausencias": listar_ausencias,
    "ausencias_de_persona": ausencias_de_persona,
    "estado_solicitudes": estado_solicitudes,
    "info_persona": info_persona,
    "equipo_de": equipo_de,
    "persona_por_cargo": persona_por_cargo,
    "cumpleanos": cumpleanos,
    "cumpleanos_de_persona": cumpleanos_de_persona,
    "dotacion": dotacion,
    "quien_esta_trabajando": quien_esta_trabajando,
    "listar_cuentas": listar_cuentas,
    "listar_beneficios": listar_beneficios,
    "beneficios_de_persona": beneficios_de_persona,
    "turno_de_persona": turno_de_persona,
    "listar_turnos": listar_turnos,
    "buscar_politica": buscar_politica,
    "salas_disponibles": salas_disponibles,
    "crear_reunion": crear_reunion,
}

_FECHA = {"type": "string", "description": "Fecha en formato AAAA-MM-DD."}

ESQUEMAS = [
    {
        "type": "function",
        "function": {
            "name": "listar_ausencias",
            "description": (
                "Quienes no estan en su jornada en un rango de fechas: vacaciones, "
                "licencias medicas, permisos e inasistencias. Para un solo dia, usar "
                "la misma fecha en desde y hasta. Si la pregunta nombra a UNA persona "
                "en particular ('¿Maria esta de vacaciones?'), usa ausencias_de_persona "
                "en vez de esta: no listes a todo el equipo para responder sobre una "
                "sola persona."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "desde": _FECHA,
                    "hasta": _FECHA,
                    "categoria": {
                        "type": "string",
                        "enum": ["vacaciones", "licencia", "permiso", "inasistencia"],
                        "description": "Omitir para incluir todas las causas.",
                    },
                },
                "required": ["desde", "hasta"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ausencias_de_persona",
            "description": (
                "Disponibilidad de UNA persona nombrada: si esta de vacaciones, con "
                "licencia, con permiso o ausente, y en que fechas. Acepta nombre, "
                "apellido o ambos. Si la pregunta es sobre su identidad ('quien es'), "
                "cargo, area o las cuentas/clientes que atiende, usa info_persona en "
                "vez de esta: esta herramienta NO trae esos datos. Si preguntan si es "
                "presencial o hibrido, tampoco es esta: eso es turno_de_persona (la "
                "modalidad de su turno, no si esta ausente)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "nombre": {"type": "string"},
                    "desde": _FECHA,
                    "hasta": _FECHA,
                },
                "required": ["nombre"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "estado_solicitudes",
            "description": (
                "En que estado esta la solicitud de vacaciones, dia "
                "administrativo, licencia o permiso de UNA persona: "
                "aprobada, pendiente o rechazada. Usar para 'en que va mi "
                "dia administrativo', 'me aprobaron las vacaciones del 20 de "
                "octubre', 'esta pendiente el permiso de X'. Distinto de "
                "ausencias_de_persona: esa dice si alguien esta o va a estar "
                "fuera (y para eso ignora lo rechazado); esta muestra "
                "CUALQUIER solicitud con su estado real, la use quien la "
                "pidio o alguien mas."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "nombre": {"type": "string"},
                    "desde": {
                        "type": "string",
                        "description": (
                            "Fecha puntual por la que preguntan (AAAA-MM-DD), si la "
                            "dieron. Omitir para buscar en una ventana amplia "
                            "(ultimos 60 dias y proximos 180)."
                        ),
                    },
                    "hasta": _FECHA,
                },
                "required": ["nombre"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "info_persona",
            "description": (
                "Quien es una persona: su cargo, area, correo corporativo y las "
                "cuentas o clientes que atiende. Usar para 'quien es X', 'que "
                "cuentas/clientes maneja o atiende X', 'a que cuenta pertenece "
                "X' o 'cual es el correo de X'. No trae ausencias ni "
                "disponibilidad, solo identidad y asignacion."
            ),
            "parameters": {
                "type": "object",
                "properties": {"nombre": {"type": "string"}},
                "required": ["nombre"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "equipo_de",
            "description": (
                "Quien compone un equipo: los integrantes de una cuenta/"
                "cliente o de un area. Usar para 'quien es del equipo/cuenta "
                "de Y' o 'muestrame el equipo que atiende Y'. Es al reves de "
                "info_persona: aca se pregunta por el grupo, no por alguien."
            ),
            "parameters": {
                "type": "object",
                "properties": {"grupo": {"type": "string"}},
                "required": ["grupo"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "persona_por_cargo",
            "description": (
                "Quien ocupa un cargo, cuando la pregunta NO nombra a nadie: "
                "'quien es el gerente de personas', 'quien es la directora de "
                "Cencosud'. Si la pregunta ya nombra a alguien, usa "
                "info_persona en vez de esta."
            ),
            "parameters": {
                "type": "object",
                "properties": {"cargo": {"type": "string"}},
                "required": ["cargo"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cumpleanos",
            "description": (
                "Quien cumple años en un dia, semana o mes, SIN nombrar a una "
                "persona en particular ('quien cumple años esta semana', "
                "'cumpleaños de octubre'). Para el cumpleaños de alguien "
                "puntual usa cumpleanos_de_persona en vez de esta. Devuelve "
                "dia y mes, nunca el año de nacimiento."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "rango": {
                        "type": "string",
                        "enum": ["hoy", "esta_semana", "este_mes"],
                        "description": (
                            "Preferir esto sobre desde/dias siempre que la "
                            "pregunta sea 'hoy', 'esta semana' o 'este mes': "
                            "calcula el rango de calendario exacto (lunes a "
                            "domingo, 1 al ultimo dia del mes)."
                        ),
                    },
                    "desde": _FECHA,
                    "dias": {"type": "integer",
                             "description": ("Cuantos dias hacia adelante incluir desde "
                                             "`desde`. Solo si no se uso `rango` (por "
                                             "ejemplo, 'en octubre' con un mes que no es "
                                             "el actual).")},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cumpleanos_de_persona",
            "description": (
                "Cuando cumple años UNA persona nombrada en la pregunta ('¿cuando "
                "cumple años X?'). Resuelve el nombre localmente, igual que "
                "ausencias_de_persona. No la uses para preguntas de disponibilidad "
                "de esa persona (vacaciones, licencias): esas van con "
                "ausencias_de_persona, no con esta."
            ),
            "parameters": {
                "type": "object",
                "properties": {"nombre": {"type": "string"}},
                "required": ["nombre"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "dotacion",
            "description": "Cantidad de personas activas en la nomina.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "quien_esta_trabajando",
            "description": (
                "Quien SI esta en su jornada (lo contrario de listar_ausencias), "
                "opcionalmente filtrado por cuenta o area. Usar para 'quien esta "
                "trabajando hoy', 'esta todo el equipo', 'quien esta disponible "
                "en X'. Si preguntan si alguien esta PRESENCIAL o HIBRIDO, no es "
                "esta herramienta: eso es la modalidad de su turno "
                "(turno_de_persona/listar_turnos), no si vino a trabajar hoy."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "desde": _FECHA,
                    "hasta": _FECHA,
                    "grupo": {"type": "string",
                             "description": "Cuenta/cliente o area. Omitir para toda la empresa."},
                },
                "required": ["desde", "hasta"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "listar_cuentas",
            "description": (
                "Nombres de todas las cuentas/clientes que administra Azerta. "
                "Usar para 'que cuentas tenemos', 'que clientes maneja Azerta'."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "listar_beneficios",
            "description": (
                "Nombres de los beneficios que existen en Azerta (modulo "
                "Beneficios de BUK: dia libre por cumpleanos, permiso por "
                "mudanza, examenes medicos preventivos, etc). Usar para 'que "
                "beneficios tiene Azerta', 'que beneficios existen', SIN "
                "nombrar a una persona. BUK NO entrega en que consiste cada "
                "uno, solo el nombre: si preguntan el detalle o las "
                "condiciones de un beneficio y no esta en el contexto de la "
                "conversacion, dilo (NO_SE) en vez de inventar en que "
                "consiste a partir del nombre."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "beneficios_de_persona",
            "description": (
                "Que beneficios ha solicitado UNA persona y en que estado "
                "esta cada solicitud (aprobado, en proceso, etc). Usar cuando "
                "la pregunta nombra a alguien: 'que beneficios tiene X', "
                "'le aprobaron el dia libre de cumpleanos a X'."
            ),
            "parameters": {
                "type": "object",
                "properties": {"nombre": {"type": "string"}},
                "required": ["nombre"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "turno_de_persona",
            "description": (
                "Turno (permanente, turno 1, turno 2), modalidad (presencial, "
                "hibrido), puesto, y si tiene presencial en una semana "
                "puntual (campo 'presencial' de la respuesta), de UNA "
                "persona. Usar SIEMPRE para 'X es presencial o hibrido', "
                "'X tiene presencial esta/la proxima semana', 'que turno "
                "tiene X', 'en que puesto se sienta X' -aunque la pregunta "
                "mencione 'hoy' ('¿X esta presencial hoy?'): esto NO es lo "
                "mismo que si vino a trabajar (eso es "
                "quien_esta_trabajando/listar_ausencias). Alguien hibrido con "
                "turno rotativo tiene presencial solo semana por medio: usa "
                "'fecha' para preguntar por una semana distinta a la actual."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "nombre": {"type": "string"},
                    "fecha": {
                        "type": "string",
                        "description": (
                            "Un dia cualquiera de la semana por la que preguntan "
                            "(AAAA-MM-DD). Omitir para la semana de hoy."
                        ),
                    },
                },
                "required": ["nombre"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "listar_turnos",
            "description": (
                "Quienes tienen un turno, modalidad o forma de trabajo "
                "determinada, SIN nombrar a una persona en particular: 'quien "
                "tiene turno presencial', 'quien es hibrido', 'quien esta en "
                "turno 1 en Digital'. Si la pregunta nombra a alguien, usa "
                "turno_de_persona en vez de esta: es al reves, aca se "
                "pregunta por el grupo."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "modalidad": {"type": "string",
                                 "description": "Ej. presencial, hibrido. Omitir para no filtrar."},
                    "forma_trabajo": {"type": "string",
                                     "description": "Ej. permanente, turno 1, turno 2. Omitir para no filtrar."},
                    "area": {"type": "string",
                            "description": "Area/equipo de la planilla de turnos. Omitir para toda la empresa."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "buscar_politica",
            "description": (
                "Busca en los documentos internos de la empresa: politicas, "
                "procedimientos, reglamento, y tambien comunicados, noticias o "
                "publicaciones internas. Usar para preguntas de como se hace "
                "algo, y para 'que se comunico/publico sobre X', 'hay alguna "
                "noticia de Y'. No lista publicaciones por fecha sin tema: es "
                "busqueda por contenido, no un calendario."
            ),
            "parameters": {
                "type": "object",
                "properties": {"consulta": {"type": "string"}},
                "required": ["consulta"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "salas_disponibles",
            "description": (
                "Que salas de reuniones estan libres u ocupadas en un rango "
                "horario. Usar cuando pidan agendar/reservar una sala, o "
                "pregunten que salas hay disponibles a cierta hora. Necesita "
                "fecha y horario exactos: si faltan, preguntalos antes de "
                "llamarla en vez de asumirlos."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "fecha": _FECHA,
                    "hora_inicio": {"type": "string", "description": "HH:MM, 24 horas."},
                    "hora_fin": {"type": "string", "description": "HH:MM, 24 horas."},
                },
                "required": ["fecha", "hora_inicio", "hora_fin"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "crear_reunion",
            "description": (
                "Reserva una sala de reuniones y crea el evento en Google "
                "Calendar. Usarla SOLO despues de mostrar la disponibilidad "
                "con salas_disponibles y que la persona elija una sala LIBRE "
                "por su nombre: nunca reservar una que salas_disponibles "
                "marco como ocupada, ni elegir la sala sin que la persona la "
                "haya nombrado."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sala": {
                        "type": "string",
                        "description": "Nombre exacto de la sala, tal cual lo devolvio salas_disponibles.",
                    },
                    "fecha": _FECHA,
                    "hora_inicio": {"type": "string", "description": "HH:MM, 24 horas."},
                    "hora_fin": {"type": "string", "description": "HH:MM, 24 horas."},
                    "titulo": {
                        "type": "string",
                        "description": "Titulo de la reunion. Si no lo dieron, arma uno breve con el motivo mencionado.",
                    },
                    "invitados": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Correos de otras personas a invitar. Omitir si no mencionaron a nadie mas.",
                    },
                },
                "required": ["sala", "fecha", "hora_inicio", "hora_fin", "titulo"],
            },
        },
    },
]
