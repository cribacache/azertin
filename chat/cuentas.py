"""Asignacion de personas a cuentas/clientes, desde la planilla de Azerta.

BUK tiene un campo "Cuentas" en custom_attributes pero esta vacio en los 98
empleados, asi que la planilla es la unica fuente. Se cruza con BUK por RUT.

El RUT se usa solo como llave: no se guarda en el directorio ni sale en ninguna
respuesta. De la planilla tampoco se leen las horas por semana, que son
informacion contractual y nadie pregunta por ellas en el chat.
"""

import logging
import re
import unicodedata
from pathlib import Path

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

HOJA = "Detalle Cuenta-Persona"
COL_CUENTA, COL_PERSONA, COL_RUT = 0, 1, 3
PRIMERA_FILA = 4  # las tres primeras son titulo y encabezado


def normalizar_rut(valor):
    return str(valor or "").replace(".", "").replace("-", "").strip().lower()


def _clave(texto):
    """Para comparar nombres de cuenta sin importar tildes ni mayusculas."""
    plano = unicodedata.normalize("NFKD", str(texto or "").lower())
    plano = "".join(c for c in plano if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", plano).strip()


def archivo():
    carpeta = Path(settings.DOCUMENTOS_DIR)
    if not carpeta.exists():
        return None
    hallazgos = sorted(carpeta.glob("*.xlsx"))
    return hallazgos[0] if hallazgos else None


def cargar(forzar=False):
    """Devuelve {clave_cuenta: {"nombre": str, "ruts": set}}.

    Cacheado por fecha de modificacion: reemplazar la planilla actualiza las
    cuentas sin reiniciar nada.
    """
    ruta = archivo()
    if ruta is None:
        return {}

    firma = (str(ruta), ruta.stat().st_mtime_ns)
    if not forzar:
        cacheado = cache.get("cuentas:mapa")
        if cacheado is not None and cacheado.get("firma") == firma:
            return cacheado["cuentas"]

    try:
        import openpyxl
    except ImportError:
        logger.warning("openpyxl no esta instalado: no se leen las cuentas")
        return {}

    try:
        libro = openpyxl.load_workbook(ruta, data_only=True, read_only=True)
        hoja = libro[HOJA] if HOJA in libro.sheetnames else libro[libro.sheetnames[0]]
        filas = list(hoja.iter_rows(min_row=PRIMERA_FILA, values_only=True))
        libro.close()
    except Exception as error:
        logger.warning("no se pudo leer %s: %s", ruta.name, error)
        return {}

    mapa = {}
    for fila in filas:
        if len(fila) <= COL_RUT:
            continue
        cuenta, rut = fila[COL_CUENTA], normalizar_rut(fila[COL_RUT])
        if not cuenta or not rut:
            continue
        clave = _clave(cuenta)
        if not clave:
            continue
        entrada = mapa.setdefault(clave, {"nombre": str(cuenta).strip(), "ruts": set()})
        entrada["ruts"].add(rut)

    cache.set("cuentas:mapa", {"firma": firma, "cuentas": mapa},
              settings.BUK_CACHE_TTL)
    return mapa


def nombres():
    """Nombres de cuenta tal como se escriben en la planilla."""
    return sorted(v["nombre"] for v in cargar().values())


LARGO_TOKEN = 3
# Palabras que aparecen en varios nombres de cuenta y no identifican a ninguna.
GENERICAS = {"banco", "grupo", "proyecto", "spa", "sa", "chile", "de", "del",
             "la", "el", "los", "las", "y"}


def _tokens(nombre):
    return {t for t in _clave(nombre).split()
            if len(t) >= LARGO_TOKEN and t not in GENERICAS}


def _indice_tokens():
    """Mapa {token: {claves de cuenta}} para buscar por parte del nombre."""
    mapa = {}
    for clave, datos in cargar().items():
        for token in _tokens(datos["nombre"]):
            mapa.setdefault(token, set()).add(clave)
    return mapa


def buscar(texto):
    """Cuenta mencionada en el texto, o None.

    Tres formas de acertar, de mas a menos especifica:
      1. El nombre completo aparece tal cual ("aguas andinas").
      2. Un token que pertenece a una sola cuenta ("santander" -> BANCO
         SANTANDER). Es el caso comun: nadie dice "el equipo de BANCO
         SANTANDER", dice "el equipo de Santander".
      3. Si el token pertenece a varias ("afp" esta en AFP Capital, AFP Cuprum
         y AFPs) no se elige ninguna: devuelve la ambiguedad para preguntar.
    """
    mapa = cargar()
    plano = f" {_clave(texto)} "

    completo = None
    for clave, datos in mapa.items():
        if len(clave) >= LARGO_TOKEN and f" {clave} " in plano:
            if completo is None or len(clave) > len(completo[0]):
                completo = (clave, datos)
    if completo:
        return completo[1]

    indice = _indice_tokens()
    palabras = set(plano.split())
    candidatas = set()
    for token in palabras & set(indice):
        candidatas |= indice[token]

    if len(candidatas) == 1:
        return mapa[next(iter(candidatas))]
    if len(candidatas) > 1:
        # se prefiere la que tenga mas tokens mencionados, y si empatan se
        # devuelve la ambiguedad para que el usuario aclare
        puntajes = {c: len(_tokens(mapa[c]["nombre"]) & palabras) for c in candidatas}
        mejor = max(puntajes.values())
        ganadoras = [c for c, n in puntajes.items() if n == mejor]
        if len(ganadoras) == 1:
            return mapa[ganadoras[0]]
        return {"ambiguas": sorted(mapa[c]["nombre"] for c in ganadoras)}
    return None


def asignar(directorio):
    """Agrega la lista de cuentas a cada persona del directorio.

    Cruza por RUT y despues lo descarta: la llave no tiene por que quedar
    guardada junto a los datos que si se muestran.
    """
    mapa = cargar()
    por_rut = {}
    for datos in mapa.values():
        for rut in datos["ruts"]:
            por_rut.setdefault(rut, []).append(datos["nombre"])

    for persona in directorio.values():
        persona["cuentas"] = sorted(por_rut.get(persona.pop("_rut", ""), []))
    return directorio
