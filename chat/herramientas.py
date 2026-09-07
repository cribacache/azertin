"""Las funciones que el modelo puede llamar.

El modelo nunca recibe la clave de BUK ni el payload crudo: solo puede invocar
estas funciones, que reutilizan la misma capa sanitizada que usa el router de
reglas. Lo que no pase por aca, el modelo no lo puede ver ni inventar.
"""

from datetime import date

from . import buk, cuentas, documentos, intents, personas

MAX_PERSONAS = 60  # techo para no mandar listados enormes al modelo


def _fecha(valor, por_defecto=None):
    try:
        return date.fromisoformat(valor)
    except (TypeError, ValueError):
        return por_defecto or date.today()


def _persona_publica(registro, directorio):
    persona = directorio.get(registro["employee_id"]) or {}
    cfg = buk.CATEGORIAS.get(registro["categoria"], {})
    return {
        "nombre": persona.get("nombre") or f"Empleado #{registro['employee_id']}",
        "cargo": persona.get("cargo") or "",
        "tipo": cfg.get("etiqueta", registro["categoria"]),
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


def info_persona(nombre):
    """Quien es una persona: cargo, area y cuentas/clientes que atiende.

    Cubre "quien es X", "que cuentas maneja X", "que clientes maneja X": son
    la misma pregunta con otras palabras, y el modelo decide cuando usarla en
    vez de tener que programar cada forma de decirla.
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


def cumpleanos(desde=None, dias=0):
    """Quien cumple anos en un rango. Solo dia y mes: el anio no se expone."""
    d = _fecha(desde)
    gente, _ = buk.cumpleanos(d, max(int(dias or 0), 0), hoy=date.today())
    return {
        "desde": d.isoformat(),
        "total": len(gente),
        "personas": [{"nombre": p["nombre"], "cargo": p["cargo"], "area": p["area"],
                      "fecha": p["fecha"], "faltan_dias": p["faltan"]}
                     for p in gente[:MAX_PERSONAS]],
    }


def dotacion():
    """Cuantas personas activas hay."""
    directorio, _ = buk.directorio()
    return {"personas_activas": len(directorio)}


def buscar_politica(consulta):
    """Busca en los documentos internos (politicas, procedimientos)."""
    secciones = documentos.buscar(consulta or "", cuantas=3)
    if not secciones:
        return {"encontrada": False}
    return {
        "encontrada": True,
        "secciones": [
            {"titulo": s["titulo"], "contenido": s["cuerpo"], "fuente": s["origen"]}
            for s in secciones
        ],
    }


FUNCIONES = {
    "listar_ausencias": listar_ausencias,
    "ausencias_de_persona": ausencias_de_persona,
    "info_persona": info_persona,
    "equipo_de": equipo_de,
    "persona_por_cargo": persona_por_cargo,
    "cumpleanos": cumpleanos,
    "dotacion": dotacion,
    "buscar_politica": buscar_politica,
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
                "la misma fecha en desde y hasta."
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
                "Situacion de UNA persona. Usar cuando la pregunta nombra a alguien. "
                "Acepta nombre, apellido o ambos."
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
            "name": "info_persona",
            "description": (
                "Quien es una persona: su cargo, area y las cuentas o clientes "
                "que atiende. Usar para 'quien es X', 'que cuentas/clientes "
                "maneja o atiende X' o 'a que cuenta pertenece X'. No trae "
                "ausencias ni disponibilidad, solo identidad y asignacion."
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
                "Quien cumple anos. Con dias=0 es solo esa fecha; con dias=30 "
                "cubre el mes siguiente. Devuelve dia y mes, nunca el anio de "
                "nacimiento."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "desde": _FECHA,
                    "dias": {"type": "integer",
                             "description": "Cuantos dias hacia adelante incluir."},
                },
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
            "name": "buscar_politica",
            "description": (
                "Busca en los documentos internos de la empresa (politicas, "
                "procedimientos, reglamento). Usar para preguntas de como se hace algo."
            ),
            "parameters": {
                "type": "object",
                "properties": {"consulta": {"type": "string"}},
                "required": ["consulta"],
            },
        },
    },
]
