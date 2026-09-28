"""Carga masiva de Turno (chat/models.py) desde la planilla "Turnos Tanica y
Digital" que ya lee chat/turnos.py, para no tener que crear cada fila a mano
desde /rrhh/turnos/.

El empleado se resuelve contra el directorio de BUK por coincidencia de
palabras del nombre (mismo tipo de matching que chat/turnos.py::buscar, pero
al reves: ahi busca en la planilla por un nombre de BUK, aca busca en BUK por
un nombre de la planilla). Si el nombre no matchea a nadie, o matchea a mas
de una persona por igual, esa fila se informa y se omite -no se adivina.

Uso:
    python manage.py importar_turnos            # dry-run: solo informa
    python manage.py importar_turnos --aplicar   # escribe los cambios

La planilla no distingue "Acuerdo" de "Conciliacion familiar" (ver
chat/models.py::Turno, docstring): todas las filas "Transitorio" quedan
como Acuerdo por defecto. Reclasificar la que corresponda a Conciliacion
familiar es trabajo manual desde el portal, despues de esta carga.
"""

from django.core.management.base import BaseCommand

from chat import buk, turnos
from chat.intents import normalizar
from chat.models import Turno

DEPARTAMENTOS_POR_AREA = {
    "asuntos publicos": Turno.DEPARTAMENTOS[0][0],
    "digital": Turno.DEPARTAMENTOS[1][0],
    "comunicaciones": Turno.DEPARTAMENTOS[2][0],
    "administracion": Turno.DEPARTAMENTOS[3][0],
}

FORMA_TRABAJO_POR_TEXTO = {
    "permanente": Turno.PERMANENTE,
    "hibrido": Turno.HIBRIDO,
    "transitorio": Turno.TRANSITORIO,
}


def _tokens(nombre):
    return {t for t in normalizar(nombre or "").split() if len(t) >= 2}


def _mejor_empleado(nombre_planilla, directorio):
    """Id de BUK que mejor matchea `nombre_planilla` por palabras en comun.
    None si nadie matchea, o si dos o mas personas empatan (ambiguo)."""
    objetivo = _tokens(nombre_planilla)
    if not objetivo:
        return None
    mejor_id, mejor_puntaje, empatados = None, 0, 0
    for emp in directorio.values():
        puntaje = len(_tokens(emp["nombre"]) & objetivo)
        if puntaje > mejor_puntaje:
            mejor_id, mejor_puntaje, empatados = emp["id"], puntaje, 1
        elif puntaje == mejor_puntaje and puntaje > 0:
            empatados += 1
    if mejor_puntaje == 0 or empatados > 1:
        return None
    return mejor_id


class Command(BaseCommand):
    help = 'Carga los turnos de la planilla de Drive a la tabla Turno. Dry-run salvo --aplicar.'

    def add_arguments(self, parser):
        parser.add_argument("--aplicar", action="store_true",
                            help="Escribe los cambios. Sin esto, solo informa que haria.")

    def handle(self, *args, **opciones):
        aplicar = opciones["aplicar"]

        directorio, _ = buk.directorio()
        if not directorio:
            self.stderr.write(self.style.ERROR("No pude leer el directorio de BUK."))
            return

        filas = turnos.cargar(forzar=True)
        if not filas:
            self.stdout.write(self.style.WARNING(
                "No hay filas en la planilla de turnos (o Drive no esta configurado)."))
            return

        ya_cargados = set(Turno.objects.values_list("buk_employee_id", flat=True))
        creados = actualizados = 0
        omitidos = {"sin_departamento": 0, "sin_forma_trabajo": 0, "sin_modalidad": 0,
                    "sin_match_buk": 0, "transitorio_sin_observacion": 0}

        for fila in filas:
            nombre = fila["nombre"]

            departamento = DEPARTAMENTOS_POR_AREA.get(normalizar(fila["area"]).strip())
            if not departamento:
                omitidos["sin_departamento"] += 1
                self.stdout.write(self.style.WARNING(
                    f'  "{nombre}": area "{fila["area"]}" no mapea a un departamento -- omitida'))
                continue

            forma_trabajo = FORMA_TRABAJO_POR_TEXTO.get(normalizar(fila["forma_trabajo"]).strip())
            if not forma_trabajo:
                omitidos["sin_forma_trabajo"] += 1
                self.stdout.write(self.style.WARNING(
                    f'  "{nombre}": forma de trabajo "{fila["forma_trabajo"]}" desconocida -- omitida'))
                continue

            modalidad_excel = normalizar(fila["modalidad"]).strip()
            if forma_trabajo == Turno.PERMANENTE:
                modalidad = Turno.PRESENCIAL
            elif forma_trabajo == Turno.HIBRIDO:
                if "turno 1" in modalidad_excel or "turno1" in modalidad_excel:
                    modalidad = Turno.TURNO_1
                elif "turno 2" in modalidad_excel or "turno2" in modalidad_excel:
                    modalidad = Turno.TURNO_2
                else:
                    omitidos["sin_modalidad"] += 1
                    self.stdout.write(self.style.WARNING(
                        f'  "{nombre}": modalidad "{fila["modalidad"]}" no es Turno 1 ni Turno 2 -- omitida'))
                    continue
            else:  # transitorio: la planilla vieja solo dice "Hibrido" a secas
                modalidad = Turno.ACUERDO

            observacion = (fila.get("observacion") or "").strip()
            if modalidad in Turno.MODALIDADES_CON_OBSERVACION_OBLIGATORIA and not observacion:
                omitidos["transitorio_sin_observacion"] += 1
                self.stdout.write(self.style.WARNING(
                    f'  "{nombre}": Transitorio sin observacion en la planilla -- omitida, cargar a mano'))
                continue

            empleado_id = _mejor_empleado(nombre, directorio)
            if empleado_id is None:
                omitidos["sin_match_buk"] += 1
                self.stdout.write(self.style.WARNING(
                    f'  "{nombre}": no encontre a nadie (o mas de uno) en BUK -- omitida'))
                continue

            existia = empleado_id in ya_cargados
            if aplicar:
                Turno.objects.update_or_create(
                    buk_employee_id=empleado_id,
                    defaults={
                        "departamento": departamento,
                        "forma_trabajo": forma_trabajo,
                        "modalidad": modalidad,
                        "numero_puesto": (fila.get("puesto") or "").strip(),
                        "observacion": observacion,
                    },
                )
            ya_cargados.add(empleado_id)
            if existia:
                actualizados += 1
            else:
                creados += 1

        total_omitidos = sum(omitidos.values())
        self.stdout.write("")
        if not aplicar:
            self.stdout.write(self.style.WARNING(
                "Dry-run: no se escribio nada. Repeti con --aplicar para guardar."))
        self.stdout.write(self.style.SUCCESS(
            f"{creados} nuevos, {actualizados} actualizados, {total_omitidos} omitidos."))
        if total_omitidos:
            detalle = ", ".join(f"{v} {k}" for k, v in omitidos.items() if v)
            self.stdout.write(f"Detalle de omitidos: {detalle}")
