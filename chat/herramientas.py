"""Las funciones que el modelo puede llamar.

El modelo nunca recibe la clave de BUK ni el payload crudo: solo puede invocar
estas funciones, que reutilizan la misma capa sanitizada que usa el router de
reglas. Lo que no pase por aca, el modelo no lo puede ver ni inventar.
"""

from datetime import date

from . import buk, documentos, personas

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
