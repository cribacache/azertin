"""Cliente de la API de BUK.

Las ausencias viven en dos endpoints distintos y no intercambiables:

  /vacations  vacaciones (legales, administrativos, progresivas, adicionales)
  /absences   licencias medicas, permisos e inasistencias

`/absences` filtra por rango en el servidor (from/to, por solapamiento).
`/vacations` no: su parametro `date` devuelve las que EMPIEZAN desde esa fecha,
asi que se usa como limite inferior con un margen mayor a la vacacion mas larga
registrada (58 dias) y el solapamiento real se resuelve aca.
"""

import re
from datetime import date

import requests
from django.conf import settings
from django.core.cache import cache

# Categorias que entiende la app, y de donde sale cada una.
CATEGORIAS = {
    "vacaciones": {"fuente": "vacations", "etiqueta": "vacaciones"},
    "licencia": {"fuente": "absences", "tipos": ("licence",), "etiqueta": "licencia médica"},
    "permiso": {"fuente": "absences", "tipos": ("leave", "paid_leave"), "etiqueta": "permiso"},
    "inasistencia": {"fuente": "absences", "tipos": ("absence",), "etiqueta": "inasistencia"},
}

TIPOS_VACACION = {
    "legales": "feriado legal",
    "dias_administrativos": "día administrativo",
    "progresivas": "vacación progresiva",
    "dias_adicionales": "día adicional",
}

TIPOS_VACACION_PLURAL = {
    "legales": "feriados legales",
    "dias_administrativos": "días administrativos",
    "progresivas": "vacaciones progresivas",
    "dias_adicionales": "días adicionales",
}

MEDIA_JORNADA = ("start_working_day", "end_working_day")

# Del cumpleanos solo se guarda "MM-DD". El anio revela la edad, que no hace
# falta para saludar a nadie y es un dato sensible: no cruza esta capa.
CAMPOS_PUBLICOS_DOC = ("id", "nombre", "apodo", "cargo", "area", "cumple")

# Campos que pueden salir del backend. El endpoint de empleados expone rut,
# direccion, cuenta bancaria, salud y prevision; nada de eso cruza esta capa.
CAMPOS_PUBLICOS = ("id", "nombre", "cargo")


class BukError(Exception):
    """Error al hablar con BUK, con mensaje ya listo para el usuario."""


def _request(path, params=None):
    if not settings.BUK_API_KEY:
        raise BukError("La conexión con BUK no está configurada. Avisa al equipo técnico.")

    try:
        response = requests.get(
            f"{settings.BUK_API_BASE}{path}",
            headers={
                settings.BUK_AUTH_HEADER: f"{settings.BUK_AUTH_PREFIX}{settings.BUK_API_KEY}"
            },
            params=params or {},
            timeout=settings.BUK_TIMEOUT,
        )
        response.raise_for_status()
        return response.json()
    except requests.HTTPError as error:
        status = error.response.status_code if error.response is not None else None
        if status == 401:
            raise BukError(
                "BUK rechazó la autenticación (401). Hay que revisar la clave, el "
                "header y los permisos de lectura de la cuenta."
            )
        if status == 400:
            raise BukError("BUK rechazó los filtros de la consulta (400).")
        raise BukError(f"BUK respondió con HTTP {status or 'error'}.")
    except requests.RequestException as error:
        raise BukError("No pude conectarme con BUK en este momento. Inténtalo de nuevo en unos minutos.")
    except ValueError:
        raise BukError("BUK respondió en un formato que no pude interpretar.")


def _paginar(path, params=None, max_paginas=25):
    """Recorre la paginacion devolviendo (registros, cantidad_requests)."""
    params = dict(params or {})
    params.setdefault("page_size", settings.BUK_PAGE_SIZE)

    registros, hechos, pagina = [], 0, 1
    while pagina <= max_paginas:
        params["page"] = pagina
        payload = _request(path, params)
        hechos += 1
        registros.extend(payload.get("data") or [])
        if not (payload.get("pagination") or {}).get("next"):
            break
        pagina += 1
    return registros, hechos


def _rut(empleado):
    """RUT normalizado, solo como llave de cruce interna."""
    valor = empleado.get("rut") or empleado.get("document_number") or ""
    return str(valor).replace(".", "").replace("-", "").strip().lower()


def _nombre(empleado):
    nombre = (empleado.get("full_name") or "").strip()
    if nombre:
        return nombre
    partes = [empleado.get("first_name"), empleado.get("surname")]
    return " ".join(p for p in partes if p).strip() or f"Empleado #{empleado.get('id')}"


def _apodo(empleado):
    """Apodo desde custom_attributes.

    OJO: custom_attributes tambien trae contacto de emergencia con telefono,
    restriccion alimentaria, inclusion y nivel de ingles. De todo eso solo sale
    el apodo; el resto no cruza esta capa.
    """
    valor = (empleado.get("custom_attributes") or {}).get("Apodo")
    return str(valor).strip() if valor else ""


def apodos_de(texto):
    """Un mismo campo puede traer varios: "Jose, JM", "Ali o Alice"."""
    if not texto:
        return []
    partes = re.split(r"\s*(?:,|/|\bo\b)\s*", texto, flags=re.IGNORECASE)
    return [p.strip() for p in partes if p.strip()]


def nombre_con_apodo(nombre, apodo):
    """Primer nombre, apodo entre comillas, y el resto: Maria "Mane" Jose Pena.

    Si el apodo ya esta dentro del nombre (Felipe, apodo "Felipe") se omite:
    repetirlo no aporta y se lee raro.
    """
    partes = (nombre or "").split()
    principal = (apodos_de(apodo) or [""])[0]
    if not principal or len(partes) < 2:
        return nombre
    from .intents import normalizar
    # por palabra completa, no por substring: "Javi" esta dentro de "Javiera"
    # pero es un apodo distinto y hay que mostrarlo
    if re.search(rf"\b{re.escape(normalizar(principal))}\b", normalizar(nombre)):
        return nombre
    return f'{partes[0]} "{principal}" {" ".join(partes[1:])}'


def _cumple(empleado):
    """Dia y mes del cumpleanos, sin el anio."""
    fecha = empleado.get("birthday") or ""
    if len(fecha) >= 10 and fecha[4] == "-" and fecha[7] == "-":
        return fecha[5:10]
    return ""


def areas(forzar=False):
    """Mapa {id: nombre} de areas. Los empleados solo traen el area_id."""
    if not forzar:
        cacheado = cache.get("buk:areas")
        if cacheado is not None:
            return cacheado, 0

    registros, hechos = _paginar("/areas", {"page_size": 100}, max_paginas=5)
    mapa = {a["id"]: (a.get("name") or "").strip()
            for a in registros if isinstance(a, dict) and a.get("id") is not None}
    cache.set("buk:areas", mapa, settings.BUK_CACHE_TTL)
    return mapa, hechos


def _cargo(empleado):
    rol = ((empleado.get("current_job") or {}).get("role")) or {}
    if isinstance(rol, dict):
        return rol.get("name") or ""
    return rol if isinstance(rol, str) else ""


def directorio(forzar=False):
    """Mapa {id: {id, nombre, cargo, area, cumple}} de activos, cacheado."""
    if not forzar:
        cacheado = cache.get("buk:directorio:v3")
        if cacheado is not None:
            return cacheado, 0

    empleados, hechos = _paginar(
        "/employees/active", {"page_size": settings.BUK_DIRECTORY_PAGE_SIZE}, max_paginas=5
    )
    nombres_area, req_areas = areas()
    hechos += req_areas

    mapa = {}
    for emp in empleados:
        if not isinstance(emp, dict) or emp.get("id") is None:
            continue
        area_id = (emp.get("current_job") or {}).get("area_id")
        apodo = _apodo(emp)
        nombre = _nombre(emp)
        mapa[emp["id"]] = {
            "id": emp["id"],
            "nombre": nombre,
            "apodo": apodo,
            # como se muestra al usuario: Maria "Mane" Jose Pena Gutierrez
            "nombre_completo": nombre_con_apodo(nombre, apodo),
            "cargo": _cargo(emp),
            "area": nombres_area.get(area_id, ""),
            "cumple": _cumple(emp),
            # el rut solo sirve para cruzar con el Excel de cuentas; se descarta
            # apenas se arma ese cruce y nunca sale en una respuesta
            "_rut": _rut(emp),
        }
    from . import cuentas
    cuentas.asignar(mapa)

    cache.set("buk:directorio:v3", mapa, settings.BUK_CACHE_TTL)
    return mapa, hechos


def cumpleanos(desde, dias=0, hoy=None):
    """Personas que cumplen entre `desde` y `desde + dias`, en orden.

    `hoy` es la referencia para contar los dias que faltan, y por defecto es
    `desde`. Importa cuando el rango empieza antes de hoy: al preguntar por
    "este mes" el dia 7, un cumpleanos del dia 6 ya paso, y contarlo desde el
    inicio del mes lo mostraria como si faltaran cinco dias.
    """
    hoy = hoy or desde
    personas, hechos = directorio()
    salida = []
    for persona in personas.values():
        cumple = persona.get("cumple") or ""
        if len(cumple) != 5:
            continue
        try:
            mes, dia = int(cumple[:2]), int(cumple[3:5])
        except ValueError:
            continue
        for anio in (desde.year, desde.year + 1):
            try:
                proximo = date(anio, mes, dia)
            except ValueError:
                # 29 de febrero en anio no bisiesto: se celebra el 28
                proximo = date(anio, 2, 28) if (mes, dia) == (2, 29) else None
            if proximo is None or proximo < desde:
                continue
            if (proximo - desde).days > dias:
                break
            faltan = (proximo - hoy).days
            salida.append({**persona, "fecha": proximo.isoformat(), "faltan": faltan})
            break

    salida.sort(key=lambda p: (p["faltan"], p["nombre"]))
    return salida, hechos


def _cubre(registro, desde, hasta):
    """True si el registro se solapa con el rango pedido."""
    inicio = registro.get("start_date") or ""
    fin = registro.get("end_date") or inicio or "9999-12-31"
    return inicio <= hasta.isoformat() and fin >= desde.isoformat()


def _normalizar_vacacion(registro):
    return {
        "employee_id": registro.get("employee_id"),
        "categoria": "vacaciones",
        "detalle": TIPOS_VACACION.get(registro.get("type"), registro.get("type") or ""),
        "start_date": registro.get("start_date"),
        "end_date": registro.get("end_date"),
        "status": registro.get("status"),
        "media_jornada": registro.get("workday_stage") in MEDIA_JORNADA,
        "dias_habiles": registro.get("working_days"),
    }


def _normalizar_ausencia(registro, categoria):
    # `licence_type` (pre_natal, accidente_comun, ...) es informacion de salud.
    # Se descarta aca, en el borde: nunca entra al resto de la aplicacion.
    return {
        "employee_id": registro.get("employee_id"),
        "categoria": categoria,
        "detalle": "",
        "start_date": registro.get("start_date"),
        "end_date": registro.get("end_date"),
        "status": registro.get("status"),
        "media_jornada": bool(registro.get("half_working_day")),
        "dias_habiles": None,
    }


def vacaciones(desde, hasta):
    """Vacaciones vigentes en el rango. Ver nota del modulo sobre `date`."""
    clave = f"buk:vac:{desde}:{hasta}"
    cacheado = cache.get(clave)
    if cacheado is not None:
        return cacheado, 0

    piso = desde - settings.BUK_VACACIONES_MARGEN
    registros, hechos = _paginar("/vacations", {"date": piso.isoformat()})
    vigentes = [
        _normalizar_vacacion(r)
        for r in registros
        if r.get("status") != "rejected" and _cubre(r, desde, hasta)
    ]
    cache.set(clave, vigentes, settings.BUK_ABSENCE_CACHE_TTL)
    return vigentes, hechos


def ausencias(desde, hasta, categorias=None):
    """Licencias, permisos e inasistencias del rango. BUK filtra por solapamiento."""
    clave = f"buk:aus:{desde}:{hasta}"
    cacheado = cache.get(clave)
    if cacheado is None:
        registros, hechos = _paginar(
            "/absences", {"from": desde.isoformat(), "to": hasta.isoformat()}
        )
        por_tipo = {}
        for categoria, cfg in CATEGORIAS.items():
            if cfg["fuente"] != "absences":
                continue
            for tipo in cfg["tipos"]:
                por_tipo[tipo] = categoria
        cacheado = [
            _normalizar_ausencia(r, por_tipo[r["type"]])
            for r in registros
            if r.get("status") != "rejected" and r.get("type") in por_tipo
        ]
        cache.set(clave, cacheado, settings.BUK_ABSENCE_CACHE_TTL)
    else:
        hechos = 0

    if categorias:
        return [r for r in cacheado if r["categoria"] in categorias], hechos
    return cacheado, hechos


def fuera(desde, hasta, categoria=None, subtipo=None):
    """Todo el que no esta en su jornada en el rango, de ambas fuentes."""
    pedidas = {categoria} if categoria else set(CATEGORIAS)
    registros, hechos = [], 0

    if "vacaciones" in pedidas:
        vac, req = vacaciones(desde, hasta)
        if subtipo:
            etiqueta = TIPOS_VACACION.get(subtipo, subtipo)
            vac = [v for v in vac if v["detalle"] == etiqueta]
        registros += vac
        hechos += req

    de_absences = pedidas & {c for c, v in CATEGORIAS.items() if v["fuente"] == "absences"}
    if de_absences:
        aus, req = ausencias(desde, hasta, de_absences)
        registros += aus
        hechos += req

    return registros, hechos
