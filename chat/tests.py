import json
import tempfile
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from django.conf import settings
from django.core.cache import cache
from django.test import TestCase, override_settings

from chat import buk, intents

# Los tests no deben depender de que archivos haya en datos/: quien no prueba
# documentos corre contra una carpeta vacia.
SIN_DOCUMENTOS = override_settings(DOCUMENTOS_DIR=tempfile.mkdtemp())

# Las fechas se calculan desde hoy: fijarlas hace que la suite empiece a fallar
# sola cuando cambia el dia, que es justo lo que paso.
HOY = date.today()
DIA = timedelta(days=1)
_f = lambda dias: (HOY + dias * DIA).isoformat()

EMPLEADOS = {
    "pagination": {"next": None},
    "data": [
        {"id": 335, "full_name": "Ana Rojas", "rut": "11.111.111-1",
         "birthday": "1979-" + _f(3)[5:],
         "custom_attributes": {"Apodo": "Mane", "Profesión": "Periodista",
                               "Contacto de emergencia": "Alguien / 994768540",
                               "Restricción alimentaria": "sin mariscos"},
         "current_job": {"role": {"name": "Analista"}, "area_id": 1,
                         "boss": {"rut": "22.222.222-2"}}},
        {"id": 468, "full_name": "Luis Soto", "email": "luis@azerta.cl",
         "birthday": "1985-" + _f(200)[5:], "rut": "22.222.222-2",
         "custom_attributes": {"Apodo": "Lucho"},
         "current_job": {"role": {"name": "Disenador"}, "area_id": 2}},
    ],
}

# /vacations: una en curso que empezo ANTES del rango (el caso que se perdia)
VACACIONES = {
    "pagination": {"next": None},
    "data": [
        {"id": 1, "employee_id": 335, "type": "legales", "status": "approved",
         "start_date": _f(-10), "end_date": _f(1),
         "workday_stage": "full_working_day", "working_days": 8.0},
        {"id": 2, "employee_id": 468, "type": "dias_administrativos", "status": "approved",
         "start_date": _f(0), "end_date": _f(0),
         "workday_stage": "start_working_day", "working_days": 0.5},
        {"id": 3, "employee_id": 468, "type": "legales", "status": "approved",
         "start_date": _f(88), "end_date": _f(92),
         "workday_stage": "full_working_day", "working_days": 5.0},
    ],
}

# /absences: licencias, permisos e inasistencias (nunca vacaciones)
AUSENCIAS = {
    "pagination": {"next": None},
    "data": [
        {"id": 9, "employee_id": 468, "type": "licence", "status": "approved",
         "start_date": _f(-2), "end_date": _f(2),
         "half_working_day": False, "licence_type": "accidente_comun"},
        {"id": 10, "employee_id": 999, "type": "licence", "status": "rejected",
         "start_date": _f(0), "end_date": _f(0),
         "half_working_day": False, "licence_type": None},
    ],
}


AREAS = {"pagination": {"next": None},
         "data": [{"id": 1, "name": "Comunicaciones"}, {"id": 2, "name": "Prensa"}]}


def fake_get(url, **kwargs):
    from unittest.mock import Mock
    if "/areas" in url:
        cuerpo = AREAS
    elif "/vacations" in url:
        cuerpo = VACACIONES
    elif "/absences" in url:
        cuerpo = AUSENCIAS
    else:
        cuerpo = EMPLEADOS
    return Mock(status_code=200, json=lambda: cuerpo, raise_for_status=lambda: None)


class IntentTests(TestCase):
    HOY = date(2026, 9, 3)

    def test_detecta_vacaciones_hoy(self):
        plan = intents.interpretar("quien esta de vacaciones hoy?", self.HOY)
        self.assertEqual(plan["intencion"], "ausencias")
        self.assertEqual(plan["categoria"], "vacaciones")
        self.assertEqual((plan["desde"], plan["hasta"]), (self.HOY, self.HOY))

    def test_detecta_licencias_y_acentos(self):
        plan = intents.interpretar("¿qué licencias médicas hay?", self.HOY)
        self.assertEqual(plan["categoria"], "licencia")

    def test_pregunta_general_no_fija_categoria(self):
        plan = intents.interpretar("¿quién no vino a trabajar hoy?", self.HOY)
        self.assertEqual(plan["intencion"], "ausencias")
        self.assertIsNone(plan["categoria"])

    def test_detecta_dias_administrativos(self):
        plan = intents.interpretar("días administrativos en septiembre", self.HOY)
        self.assertEqual(plan["categoria"], "vacaciones")
        self.assertEqual(plan["subtipo"], "dias_administrativos")

    def test_detecta_rango_de_mes(self):
        plan = intents.interpretar("vacaciones en octubre", self.HOY)
        self.assertEqual(plan["desde"], date(2026, 10, 1))
        self.assertEqual(plan["hasta"], date(2026, 10, 31))

    def test_detecta_semana(self):
        plan = intents.interpretar("quien esta fuera esta semana", self.HOY)
        self.assertEqual(plan["desde"], date(2026, 8, 31))  # lunes
        self.assertEqual(plan["hasta"], date(2026, 9, 6))

    def test_fecha_explicita(self):
        plan = intents.interpretar("vacaciones el 2026-09-07", self.HOY)
        self.assertEqual(plan["desde"], date(2026, 9, 7))

    def test_mensaje_sin_relacion_cae_en_ayuda(self):
        self.assertEqual(intents.interpretar("cuanto es el aguinaldo", self.HOY)["intencion"],
                         "ayuda")

    def test_reconoce_la_cortesia_sin_gastar_modelo(self):
        for mensaje, esperado in (
            ("hola", "saludo"),
            ("¡Hola!", "saludo"),
            ("buenas tardes", "saludo"),
            ("¿cómo estás?", "saludo"),
            ("gracias", "gracias"),
            ("muchas gracias, perfecto", "gracias"),
            ("chao", "despedida"),
            ("¿quién eres?", "identidad"),
            ("¿en qué me puedes ayudar?", "identidad"),
        ):
            self.assertEqual(intents.interpretar(mensaje, self.HOY)["intencion"], esperado,
                             mensaje)

    def test_la_cortesia_no_secuestra_preguntas_reales(self):
        """"ayuda" y "gracias" aparecen dentro de consultas de verdad."""
        for mensaje in ("ayudame con las vacaciones de octubre",
                        "hola, ¿quién está fuera hoy?",
                        "gracias, ¿y quién está de vacaciones mañana?"):
            self.assertEqual(intents.interpretar(mensaje, self.HOY)["intencion"], "ausencias",
                             mensaje)


@SIN_DOCUMENTOS
class ChatViewTests(TestCase):
    def setUp(self):
        cache.clear()

    def _preguntar(self, mensaje):
        import json as _json
        return self.client.post("/api/chat/", data=_json.dumps({"message": mensaje}),
                                content_type="application/json").json()

    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_incluye_vacacion_ya_empezada(self, mocked):
        """El caso que fallaba: una vacacion en curso que empezo antes de hoy."""
        cuerpo = self._preguntar("quien esta de vacaciones hoy")
        nombres = [i["nombre"] for i in cuerpo["items"]]
        self.assertIn('Ana "Mane" Rojas', nombres)     # 24-ago -> 4-sep, en curso
        self.assertIn('Luis "Lucho" Soto', nombres)     # dia administrativo de hoy
        self.assertEqual(len(cuerpo["items"]), 2)  # la de diciembre queda fuera

    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_vacaciones_no_salen_de_absences(self, mocked):
        """Las vacaciones vienen de /vacations, no del paid_leave de /absences."""
        self._preguntar("quien esta de vacaciones hoy")
        urls = [c.args[0] for c in mocked.call_args_list]
        self.assertTrue(any("/vacations" in u for u in urls))
        self.assertFalse(any("/absences" in u for u in urls))

    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_fuera_combina_ambas_fuentes(self, mocked):
        cuerpo = self._preguntar("quien esta fuera hoy")
        tipos = {i["tipo"] for i in cuerpo["items"]}
        self.assertEqual(tipos, {"vacaciones", "licencia médica"})
        self.assertEqual(len(cuerpo["items"]), 3)  # una fila por persona y tipo

    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_subtipo_administrativo(self, mocked):
        cuerpo = self._preguntar("dias administrativos hoy")
        self.assertEqual(len(cuerpo["items"]), 1)
        self.assertEqual(cuerpo["items"][0]["nombre"], 'Luis "Lucho" Soto')
        self.assertTrue(cuerpo["items"][0]["media_jornada"])

    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_vacaciones_piden_margen_hacia_atras(self, mocked):
        self._preguntar("vacaciones hoy")
        llamada = next(c for c in mocked.call_args_list if "/vacations" in c.args[0])
        self.assertLess(llamada.kwargs["params"]["date"], HOY.isoformat())  # pide desde antes

    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_no_expone_datos_sensibles(self, mocked):
        response = self.client.post(
            "/api/chat/", data='{"message":"quien esta fuera hoy"}',
            content_type="application/json",
        )
        crudo = response.content.decode()
        for sensible in ("11.111.111-1", "22.222.222-2", "luis@azerta.cl", "rut"):
            self.assertNotIn(sensible, crudo)

    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_directorio_se_cachea_entre_mensajes(self, mocked):
        for _ in range(3):
            self.client.post("/api/chat/", data='{"message":"quien esta fuera hoy"}',
                             content_type="application/json")
        urls = [c.args[0] for c in mocked.call_args_list]
        self.assertEqual(sum("/employees/active" in u for u in urls), 1)

    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_status_reporta_nomina(self, mocked):
        response = self.client.get("/api/status/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["personas_activas"], 2)


@SIN_DOCUMENTOS
class ConfidencialidadTests(TestCase):
    def setUp(self):
        cache.clear()

    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_no_expone_el_motivo_de_la_licencia(self, mocked):
        """licence_type es informacion de salud y no debe salir nunca."""
        response = self.client.post(
            "/api/chat/", data='{"message":"licencias hoy"}',
            content_type="application/json",
        )
        crudo = response.content.decode()
        for reservado in ("accidente_comun", "accidente comun", "pre_natal",
                          "post natal", "parental", "licence_type"):
            self.assertNotIn(reservado, crudo)
        self.assertEqual(response.json()["items"][0]["detalle"], "")


@SIN_DOCUMENTOS
class PersonaTests(TestCase):
    def setUp(self):
        cache.clear()

    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_pregunta_por_una_persona_responde_en_texto(self, mocked):
        cuerpo = self.client.post(
            "/api/chat/", data='{"message":"esta Ana de vacaciones hoy?"}',
            content_type="application/json",
        ).json()
        self.assertEqual(cuerpo["meta"]["intencion"], "persona")
        self.assertIn("Ana", cuerpo["answer"])
        self.assertNotIn("Luis Soto", cuerpo["answer"])  # no lista a los demas
        self.assertEqual(len(cuerpo["items"]), 1)

    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_persona_sin_ausencias_lo_dice(self, mocked):
        cuerpo = self.client.post(
            "/api/chat/", data='{"message":"esta Ana Rojas de vacaciones el 2026-11-11?"}',
            content_type="application/json",
        ).json()
        self.assertIn("no registra ausencias", cuerpo["answer"])
        self.assertIn("Ana", cuerpo["answer"])


@SIN_DOCUMENTOS
class SinDatosTests(TestCase):
    def setUp(self):
        cache.clear()

    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_avisa_y_registra_la_consulta(self, mock_buk):
        from chat.models import ConsultaNoResuelta
        cuerpo = self.client.post(
            "/api/chat/", data='{"message":"cuanto es el bono de fin de anio?"}',
            content_type="application/json",
        ).json()
        self.assertIn("Todavía no tengo esa información", cuerpo["answer"])
        self.assertTrue(cuerpo["meta"]["registrada"])
        self.assertEqual(ConsultaNoResuelta.objects.count(), 1)

    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_repetir_la_pregunta_agrupa_en_una_fila(self, mock_buk):
        from chat.models import ConsultaNoResuelta
        for texto in ('{"message":"cuanto es el aguinaldo?"}',
                      '{"message":"Cuanto es el AGUINALDO?"}'):
            self.client.post("/api/chat/", data=texto, content_type="application/json")
        self.assertEqual(ConsultaNoResuelta.objects.count(), 1)
        self.assertEqual(ConsultaNoResuelta.objects.first().veces, 2)


class DocumentosTests(TestCase):
    def setUp(self):
        cache.clear()

    def _con_documento(self, texto, nombre="politica.md"):
        import tempfile
        from django.test import override_settings
        carpeta = tempfile.mkdtemp()
        (Path(carpeta) / nombre).write_text(texto, encoding="utf-8")
        return override_settings(DOCUMENTOS_DIR=carpeta)

    def test_responde_desde_el_documento(self):
        doc = "# Politica\n\n## Como pedir vacaciones\n\nCon quince dias de anticipacion.\n"
        with self._con_documento(doc):
            cuerpo = self.client.post(
                "/api/chat/", data='{"message":"como pido vacaciones?"}',
                content_type="application/json",
            ).json()
        self.assertEqual(cuerpo["meta"]["intencion"], "documento")
        self.assertIn("quince dias", cuerpo["answer"])

    def test_procedimiento_no_se_confunde_con_personas(self):
        """'como pido vacaciones' no debe listar a quien esta de vacaciones."""
        self.assertTrue(intents.es_procedimiento("¿cómo pido vacaciones?"))
        self.assertFalse(intents.es_procedimiento("¿quién está de vacaciones hoy?"))


class RegistroSelectivoTests(TestCase):
    def setUp(self):
        cache.clear()

    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_no_registra_una_respuesta_negativa_correcta(self, mocked):
        """'X no tiene ausencias' se responde bien; no es una consulta pendiente."""
        from chat.models import ConsultaNoResuelta
        self.client.post(
            "/api/chat/", data='{"message":"esta Ana Rojas de vacaciones el 2026-11-11?"}',
            content_type="application/json",
        )
        self.assertEqual(ConsultaNoResuelta.objects.count(), 0)


@SIN_DOCUMENTOS
class AsistenteTests(TestCase):
    def setUp(self):
        cache.clear()

    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_la_herramienta_no_entrega_el_motivo_de_la_licencia(self, mock_buk):
        from chat import herramientas
        datos = herramientas.listar_ausencias(_f(0), _f(0))
        crudo = json.dumps(datos, ensure_ascii=False)
        for reservado in ("accidente_comun", "accidente comun", "licence_type", "post natal"):
            self.assertNotIn(reservado, crudo)


@SIN_DOCUMENTOS
class CacheRespuestasTests(TestCase):
    def setUp(self):
        cache.clear()

    def _preguntar(self, texto):
        return self.client.post("/api/chat/", data=json.dumps({"message": texto}),
                                content_type="application/json").json()

    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_la_segunda_vez_no_consulta_buk(self, mocked):
        self._preguntar("quien esta fuera hoy")
        llamadas_primera = len(mocked.call_args_list)
        cuerpo = self._preguntar("quien esta fuera hoy")
        self.assertTrue(cuerpo["meta"]["desde_cache"])
        self.assertEqual(cuerpo["meta"]["requests_buk"], 0)
        self.assertEqual(len(mocked.call_args_list), llamadas_primera)  # sin red nueva

    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_ignora_acentos_mayusculas_y_puntuacion(self, mocked):
        primera = self._preguntar("quien esta fuera hoy")
        self.assertFalse(primera["meta"]["desde_cache"])
        for variante in ("¿Quién está fuera hoy?", "QUIEN ESTA FUERA HOY!!",
                         "  quien   esta  fuera  hoy  "):
            self.assertTrue(self._preguntar(variante)["meta"]["desde_cache"], variante)

    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_no_cachea_entre_dias_distintos(self, mocked):
        """La respuesta a "hoy" no puede servirse manana."""
        from chat import respuestas
        hoy, manana = date(2026, 9, 3), date(2026, 9, 4)
        self.assertNotEqual(respuestas.clave("quien esta fuera hoy", hoy),
                            respuestas.clave("quien esta fuera hoy", manana))

    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_no_cachea_las_respuestas_sin_datos(self, mocked):
        self._preguntar("cuanto es el aguinaldo?")
        cuerpo = self._preguntar("cuanto es el aguinaldo?")
        self.assertFalse(cuerpo["meta"]["desde_cache"])

    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_registra_las_preguntas_con_su_frecuencia(self, mocked):
        from chat.models import Pregunta
        for _ in range(3):
            self._preguntar("quien esta fuera hoy")
        fila = Pregunta.objects.get(mensaje_normalizado__contains="fuera hoy")
        self.assertEqual(fila.veces, 3)
        self.assertEqual(fila.veces_cache, 2)  # la primera no vino de cache

    @override_settings(GEMINI_API_KEY="AIza-prueba",
                       ASISTENTE_SIEMPRE=True, ASISTENTE_ANONIMIZAR=False)
    @patch("chat.buk.requests.get", side_effect=fake_get)
    @patch("chat.asistente._cliente_gemini")
    def test_modo_siempre_manda_todo_al_modelo(self, mock_cliente, mock_buk):
        generar = mock_cliente.return_value.models.generate_content
        generar.return_value = _respuesta_gemini(texto="Hoy hay 3 personas fuera.")
        cuerpo = self._preguntar("quien esta fuera hoy")
        self.assertEqual(cuerpo["meta"]["intencion"], "modelo")


@SIN_DOCUMENTOS
class ComplejidadTests(TestCase):
    """Las reglas deben ceder en vez de contestar a medias."""

    def setUp(self):
        cache.clear()

    def test_reconoce_lo_que_no_puede_resolver(self):
        for pregunta in ("compara agosto contra septiembre",
                         "¿qué área tiene más ausencias?",
                         "ranking de areas con mas licencias",
                         "¿por qué hay tanta gente fuera?",
                         "¿cuántos días de vacaciones le quedan a Ana?"):
            self.assertTrue(intents.es_compleja(pregunta), pregunta)

    def test_filtrar_por_un_area_lo_resuelven_las_reglas(self):
        """Antes iba al modelo; ahora el router sabe filtrar por área."""
        for pregunta in ("¿cuántas personas de comunicaciones están fuera?",
                         "¿quién está de vacaciones en asuntos públicos?",
                         "¿quién está trabajando hoy en prensa?"):
            self.assertFalse(intents.es_compleja(pregunta), pregunta)

    def test_no_marca_las_preguntas_simples(self):
        for pregunta in ("¿quién está fuera hoy?", "licencias esta semana",
                         "vacaciones en octubre", "¿cuántas personas hay activas?"):
            self.assertFalse(intents.es_compleja(pregunta), pregunta)

    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_sin_modelo_lo_dice_en_vez_de_inventar(self, mocked):
        cuerpo = self.client.post(
            "/api/chat/", data='{"message":"compara agosto contra septiembre"}',
            content_type="application/json",
        ).json()
        self.assertEqual(cuerpo["meta"]["intencion"], "sin_datos")

    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_dotacion_no_secuestra_preguntas_de_ausencia(self, mocked):
        plan = intents.interpretar("cuantas personas estan fuera hoy", date(2026, 9, 3))
        self.assertEqual(plan["intencion"], "ausencias")


class _PedidoGemini:
    def __init__(self, nombre, args):
        self.name = nombre
        self.args = args


def _respuesta_gemini(texto=None, llamadas=None, contenido=None):
    return Mock(text=texto, function_calls=llamadas or [],
                candidates=[Mock(content=contenido or Mock())])


@SIN_DOCUMENTOS
@override_settings(GEMINI_API_KEY="AIza-prueba",
                   ASISTENTE_ANONIMIZAR=False, ASISTENTE_SIEMPRE=False)
class GeminiTests(TestCase):
    """El SDK se simula: la suite no consume cuota gratuita."""

    def setUp(self):
        cache.clear()

    def _preguntar(self, texto):
        return self.client.post("/api/chat/", data=json.dumps({"message": texto}),
                                content_type="application/json").json()

    def test_con_clave_esta_disponible(self):
        from chat import asistente
        self.assertTrue(asistente.disponible())
        self.assertEqual(asistente.modelo(), settings.GEMINI_MODEL)

    def test_los_esquemas_se_traducen_al_formato_de_google(self):
        from chat import asistente, herramientas
        tools = asistente._declaraciones_gemini()
        declaradas = {f.name for f in tools[0].function_declarations}
        self.assertEqual(declaradas, set(herramientas.FUNCIONES))

    @patch("chat.buk.requests.get", side_effect=fake_get)
    @patch("chat.asistente._cliente_gemini")
    def test_llama_a_la_herramienta_y_responde(self, mock_cliente, mock_buk):
        generar = mock_cliente.return_value.models.generate_content
        generar.side_effect = [
            _respuesta_gemini(llamadas=[_PedidoGemini(
                "listar_ausencias", {"desde": _f(0), "hasta": _f(0)})]),
            _respuesta_gemini(texto="Hay 3 personas fuera hoy."),
        ]
        cuerpo = self._preguntar("hazme un resumen de la carga del equipo")
        self.assertEqual(cuerpo["meta"]["intencion"], "modelo")
        self.assertEqual(cuerpo["meta"]["herramientas"], ["listar_ausencias"])
        self.assertIn("3 personas", cuerpo["answer"])

    @override_settings(ASISTENTE_ANONIMIZAR=True)
    @patch("chat.buk.requests.get", side_effect=fake_get)
    @patch("chat.asistente._cliente_gemini")
    def test_anonimiza_y_restituye(self, mock_cliente, mock_buk):
        generar = mock_cliente.return_value.models.generate_content
        generar.side_effect = [
            _respuesta_gemini(llamadas=[_PedidoGemini(
                "listar_ausencias", {"desde": _f(0), "hasta": _f(0)})]),
            _respuesta_gemini(texto="Persona 1 esta de vacaciones."),
        ]
        cuerpo = self._preguntar("hazme un resumen de la carga del equipo")
        self.assertIn("Ana", cuerpo["answer"])

    @patch("chat.buk.requests.get", side_effect=fake_get)
    @patch("chat.asistente._cliente_gemini")
    def test_si_gemini_falla_la_app_responde_igual(self, mock_cliente, mock_buk):
        mock_cliente.return_value.models.generate_content.side_effect = RuntimeError("429")
        cuerpo = self._preguntar("hazme un resumen de la carga del equipo")
        self.assertEqual(cuerpo["meta"]["intencion"], "sin_datos")

    @patch("chat.buk.requests.get", side_effect=fake_get)
    @patch("chat.asistente._cliente_gemini")
    def test_las_reglas_no_consumen_cuota(self, mock_cliente, mock_buk):
        self._preguntar("quien esta fuera hoy")
        mock_cliente.assert_not_called()


class DocumentoTextoPlanoTests(TestCase):
    """Un .txt exportado de PDF: sin titulos markdown y con ligaduras."""

    def setUp(self):
        cache.clear()

    def _con(self, texto):
        carpeta = tempfile.mkdtemp()
        (Path(carpeta) / "politica.txt").write_text(texto, encoding="utf-8")
        return override_settings(DOCUMENTOS_DIR=carpeta)

    DOC = (
        "Politica de Vacaciones\nAzerta\n"
        "Vigencia:\nEsta politica rige desde el 1 de julio de 2025 para toda la oficina "
        "y reemplaza cualquier version anterior del documento.\n"
        "Aspectos legales:\nLos trabajadores tienen derecho a quince dias habiles de "
        "feriado anual con remuneracion integra segun el Codigo del Trabajo.\n"
        "III. Dias administrativos\nCada colaborador tiene derecho a un dia "
        "administrativo por semestre, que no se acumula al semestre siguiente.\n"
    )

    def test_parte_un_txt_sin_titulos_markdown(self):
        from chat import documentos
        with self._con(self.DOC):
            titulos = [s["titulo"] for s in documentos.cargar()]
        self.assertIn("Vigencia", titulos)
        self.assertIn("Aspectos legales", titulos)
        self.assertIn("III. Dias administrativos", titulos)

    def test_descarta_la_portada(self):
        from chat import documentos
        with self._con(self.DOC):
            cuerpos = [s["cuerpo"] for s in documentos.cargar()]
        self.assertFalse(any(c.startswith("Politica de Vacaciones\nAzerta") for c in cuerpos))

    def test_ignora_el_leeme(self):
        from chat import documentos
        carpeta = tempfile.mkdtemp()
        (Path(carpeta) / "LEEME.md").write_text("# Guia\n\nComo usar la carpeta.\n" * 9,
                                                encoding="utf-8")
        with override_settings(DOCUMENTOS_DIR=carpeta):
            self.assertEqual(documentos.cargar(), [])

    def test_las_ligaduras_de_pdf_no_rompen_la_busqueda(self):
        """Un PDF exportado escribe "planiﬁcacion" con la ligadura U+FB01."""
        from chat import documentos
        doc = ("Planiﬁcacion:\nLa planiﬁcacion de vacaciones la coordina cada "
               "director de cuenta antes de ingresar la solicitud en la plataforma.\n")
        with self._con(doc):
            seccion = documentos.responder("como es la planificacion de vacaciones")
        self.assertIsNotNone(seccion)

    def test_la_seccion_larga_no_gana_por_volumen(self):
        from chat import documentos
        doc = ("Generalidades:\n" + "vacaciones dias feriado solicitud equipo " * 40 + "\n"
               "Enfermedad:\nSi el colaborador se enferma durante sus vacaciones puede "
               "solicitar la reprogramacion presentando la licencia.\n")
        with self._con(doc):
            seccion = documentos.responder("que pasa si me enfermo en vacaciones")
        self.assertEqual(seccion["titulo"], "Enfermedad")

    def test_la_herramienta_entrega_varias_secciones(self):
        from chat import herramientas
        with self._con(self.DOC):
            salida = herramientas.buscar_politica("dias administrativos y feriado legal")
        self.assertTrue(salida["encontrada"])
        self.assertGreaterEqual(len(salida["secciones"]), 1)
        self.assertIn("titulo", salida["secciones"][0])


class RuteoProcedimientoTests(TestCase):
    def test_las_marcas_de_ausencia_mandan_sobre_las_de_procedimiento(self):
        # "no puedo contar" contiene "puedo", que es marca de procedimiento
        self.assertFalse(intents.es_procedimiento("¿con quién no puedo contar esta semana?"))
        self.assertFalse(intents.es_procedimiento("¿quién está fuera hoy?"))

    def test_sigue_reconociendo_las_de_procedimiento(self):
        self.assertTrue(intents.es_procedimiento("¿cómo pido vacaciones?"))
        self.assertTrue(intents.es_procedimiento("¿a quién aviso por una licencia médica?"))
        self.assertTrue(intents.es_procedimiento("¿qué son los días administrativos?"))


@SIN_DOCUMENTOS
class RuteoComplejoTests(TestCase):
    def setUp(self):
        cache.clear()

    @override_settings(GEMINI_API_KEY="AIza-prueba")
    @patch("chat.buk.requests.get", side_effect=fake_get)
    @patch("chat.asistente._cliente_gemini")
    def test_una_comparacion_de_datos_va_al_modelo(self, mock_cliente, mock_buk):
        generar = mock_cliente.return_value.models.generate_content
        generar.side_effect = [_respuesta_gemini(texto="En agosto hubo mas.")]
        cuerpo = self.client.post(
            "/api/chat/",
            data=json.dumps({"message": "compara cuanta gente estuvo fuera en agosto contra septiembre"}),
            content_type="application/json",
        ).json()
        self.assertEqual(cuerpo["meta"]["intencion"], "modelo")


class TimeoutTests(TestCase):
    @override_settings(GEMINI_API_KEY="AIza-prueba",
                       ASISTENTE_TIMEOUT=12)
    def test_el_cliente_de_gemini_lleva_timeout(self):
        """Sin timeout, una llamada colgada deja la pregunta esperando siempre."""
        from chat import asistente
        cliente = asistente._cliente_gemini()
        opciones = cliente._api_client._http_options
        self.assertEqual(opciones.timeout, 12000)  # el SDK los cuenta en ms


@SIN_DOCUMENTOS
@override_settings(GEMINI_API_KEY="AIza-prueba",
                   ASISTENTE_ANONIMIZAR=False)
class GeminiFirmaTests(TestCase):
    """Gemini 3.x firma cada functionCall y exige recibir la firma de vuelta."""

    def setUp(self):
        cache.clear()

    @patch("chat.buk.requests.get", side_effect=fake_get)
    @patch("chat.asistente._cliente_gemini")
    def test_devuelve_el_contenido_original_del_modelo(self, mock_cliente, mock_buk):
        contenido = Mock(name="contenido-firmado")
        primera = Mock(
            text=None,
            function_calls=[_PedidoGemini("listar_ausencias",
                                          {"desde": _f(0), "hasta": _f(0)})],
            candidates=[Mock(content=contenido)],
        )
        generar = mock_cliente.return_value.models.generate_content
        generar.side_effect = [primera, _respuesta_gemini(texto="Listo.")]

        self.client.post("/api/chat/",
                         data=json.dumps({"message": "hazme un resumen de la carga del equipo"}),
                         content_type="application/json")

        # el segundo turno debe reenviar el objeto tal cual lo devolvio la API,
        # no una reconstruccion que pierde el thought_signature
        historial = generar.call_args_list[1].kwargs["contents"]
        self.assertIn(contenido, historial)


@SIN_DOCUMENTOS
@override_settings(GEMINI_API_KEY="AIza-prueba",
                   ASISTENTE_ANONIMIZAR=False, ASISTENTE_SIEMPRE=False)
class CombinacionTests(TestCase):
    """Reglas y modelo trabajando juntos: uno cubre al otro."""

    def setUp(self):
        cache.clear()

    def _preguntar(self, texto):
        return self.client.post("/api/chat/", data=json.dumps({"message": texto}),
                                content_type="application/json").json()

    @patch("chat.buk.requests.get", side_effect=fake_get)
    @patch("chat.asistente._cliente_gemini")
    def test_si_el_modelo_falla_responden_las_reglas(self, mock_cliente, mock_buk):
        """Antes esto terminaba en 'no tengo esa informacion'."""
        mock_cliente.return_value.models.generate_content.side_effect = RuntimeError("503")
        cuerpo = self._preguntar("compara cuánta gente estuvo fuera este mes")
        self.assertEqual(cuerpo["meta"]["intencion"], "reglas_respaldo")
        self.assertTrue(cuerpo["meta"]["parcial"])
        self.assertTrue(cuerpo["items"])  # trae el detalle igual

    @patch("chat.buk.requests.get", side_effect=fake_get)
    @patch("chat.asistente._cliente_gemini")
    def test_al_modelo_se_le_adelantan_los_datos(self, mock_cliente, mock_buk):
        """Con el contexto ya resuelto, no necesita una vuelta extra."""
        generar = mock_cliente.return_value.models.generate_content
        generar.side_effect = [_respuesta_gemini(texto="Este mes hubo más ausencias.")]
        cuerpo = self._preguntar("compara cuánta gente estuvo fuera este mes")
        self.assertEqual(cuerpo["meta"]["intencion"], "modelo")
        self.assertTrue(cuerpo["meta"]["con_contexto"])
        self.assertEqual(generar.call_count, 1)  # una sola llamada, no dos
        enviado = str(generar.call_args.kwargs["contents"])
        self.assertIn("DATOS YA CONSULTADOS", enviado)

    @patch("chat.buk.requests.get", side_effect=fake_get)
    @patch("chat.asistente._cliente_gemini")
    def test_tras_varias_fallas_deja_de_llamar_al_modelo(self, mock_cliente, mock_buk):
        """Cortacircuitos: no esperar el timeout en cada pregunta."""
        from chat import asistente
        mock_cliente.return_value.models.generate_content.side_effect = RuntimeError("503")
        for i in range(settings.ASISTENTE_FALLAS_MAX):
            self._preguntar(f"compara las ausencias, intento {i}")
        self.assertTrue(asistente.en_pausa())
        self.assertFalse(asistente.disponible())

        llamadas = mock_cliente.call_count
        self._preguntar("compara otra cosa distinta")
        self.assertEqual(mock_cliente.call_count, llamadas)  # ya no lo intenta

    @patch("chat.buk.requests.get", side_effect=fake_get)
    @patch("chat.asistente._cliente_gemini")
    def test_un_exito_reactiva_el_modelo(self, mock_cliente, mock_buk):
        from chat import asistente
        generar = mock_cliente.return_value.models.generate_content
        generar.side_effect = [RuntimeError("503"),
                               _respuesta_gemini(texto="Listo.")]
        self._preguntar("compara las ausencias de este mes")
        self._preguntar("compara las ausencias de la semana")
        self.assertFalse(asistente.en_pausa())

    @patch("chat.buk.requests.get", side_effect=fake_get)
    @patch("chat.asistente._cliente_gemini")
    def test_las_preguntas_simples_no_tocan_el_modelo(self, mock_cliente, mock_buk):
        self._preguntar("quien esta fuera hoy")
        mock_cliente.assert_not_called()

    @override_settings(ASISTENTE_ANONIMIZAR=True)
    @patch("chat.buk.requests.get", side_effect=fake_get)
    @patch("chat.asistente._cliente_gemini")
    def test_el_contexto_adelantado_tambien_se_anonimiza(self, mock_cliente, mock_buk):
        generar = mock_cliente.return_value.models.generate_content
        generar.side_effect = [_respuesta_gemini(texto="Persona 1 está fuera.")]
        cuerpo = self._preguntar("compara cuánta gente estuvo fuera este mes")
        enviado = str(generar.call_args.kwargs["contents"])
        self.assertNotIn("Ana Rojas", enviado)
        self.assertIn("Ana", cuerpo["answer"])

    @patch("chat.buk.requests.get", side_effect=fake_get)
    @patch("chat.asistente._cliente_gemini")
    def test_la_respuesta_de_respaldo_no_se_cachea(self, mock_cliente, mock_buk):
        """Si se cacheara, seguiria dando la version degradada tras recuperarse."""
        generar = mock_cliente.return_value.models.generate_content
        generar.side_effect = [RuntimeError("504"),
                               _respuesta_gemini(texto="Ahora sí: hubo 3 personas fuera.")]
        primera = self._preguntar("compara cuánta gente estuvo fuera este mes")
        self.assertEqual(primera["meta"]["intencion"], "reglas_respaldo")

        segunda = self._preguntar("compara cuánta gente estuvo fuera este mes")
        self.assertFalse(segunda["meta"].get("desde_cache"))
        self.assertEqual(segunda["meta"]["intencion"], "modelo")


@SIN_DOCUMENTOS
class CortesiaTests(TestCase):
    def setUp(self):
        cache.clear()

    def _preguntar(self, texto):
        return self.client.post("/api/chat/", data=json.dumps({"message": texto}),
                                content_type="application/json").json()

    @override_settings(GEMINI_API_KEY="AIza-prueba",
                       ASISTENTE_SIEMPRE=False)
    @patch("chat.buk.requests.get", side_effect=fake_get)
    @patch("chat.asistente._cliente_gemini")
    def test_un_saludo_no_gasta_modelo_ni_buk(self, mock_cliente, mock_buk):
        cuerpo = self._preguntar("Hola")
        self.assertEqual(cuerpo["meta"]["intencion"], "cortesia")
        self.assertEqual(cuerpo["meta"]["requests_buk"], 0)
        mock_cliente.assert_not_called()
        mock_buk.assert_not_called()

    def test_devuelve_el_mismo_saludo(self):
        self.assertTrue(self._preguntar("buenas tardes")["answer"].startswith("Buenas tardes"))
        self.assertTrue(self._preguntar("buenos días")["answer"].startswith("Buenos días"))
        self.assertTrue(self._preguntar("hola")["answer"].startswith("Hola"))

    def test_no_se_registra_como_consulta_sin_responder(self):
        from chat.models import ConsultaNoResuelta
        for m in ("hola", "gracias", "chao", "¿quién eres?"):
            self._preguntar(m)
        self.assertEqual(ConsultaNoResuelta.objects.count(), 0)


class PdfTests(TestCase):
    def setUp(self):
        cache.clear()

    def _pdf(self, paginas):
        """Arma un PDF de prueba con pymupdf."""
        import pymupdf
        carpeta = tempfile.mkdtemp()
        ruta = Path(carpeta) / "manual.pdf"
        doc = pymupdf.open()
        for texto in paginas:
            pagina = doc.new_page()
            pagina.insert_text((60, 80), texto, fontsize=11)
        doc.save(ruta)
        doc.close()
        return carpeta, ruta

    def test_extrae_texto_de_un_pdf(self):
        from chat import pdf
        _, ruta = self._pdf(["Vigencia:\nRige desde julio de 2025."])
        texto = pdf.extraer(ruta)
        self.assertIn("Vigencia", texto)
        self.assertIn("julio de 2025", texto)

    def test_un_pdf_entra_al_indice_de_documentos(self):
        from chat import documentos
        carpeta, _ = self._pdf([
            "Dias administrativos:\nCada colaborador tiene un dia administrativo "
            "por semestre y no se acumula al siguiente."
        ])
        with override_settings(DOCUMENTOS_DIR=carpeta):
            seccion = documentos.responder("dia administrativo por semestre")
        self.assertIsNotNone(seccion)
        self.assertIn("semestre", seccion["cuerpo"])

    def test_descarta_encabezados_repetidos(self):
        from chat import pdf
        paginas = [f"Azerta - Confidencial\nContenido de la pagina {i}."
                   for i in range(1, 6)]
        _, ruta = self._pdf(paginas)
        texto = pdf.extraer(ruta)
        self.assertLessEqual(texto.count("Azerta - Confidencial"), 1)
        self.assertIn("pagina 3", texto)

    def test_un_pdf_sin_texto_no_rompe_nada(self):
        from chat import documentos, pdf
        import pymupdf
        carpeta = tempfile.mkdtemp()
        ruta = Path(carpeta) / "escaneado.pdf"
        doc = pymupdf.open()
        doc.new_page()          # pagina en blanco: simula un escaneo sin OCR
        doc.save(ruta)
        doc.close()
        self.assertEqual(pdf.extraer(ruta), "")
        with override_settings(DOCUMENTOS_DIR=carpeta):
            self.assertEqual(documentos.cargar(forzar=True), [])


class EmbeddingsTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_sin_clave_no_se_activan(self):
        from chat import embeddings
        self.assertFalse(embeddings.disponible())

    def test_el_coseno_ordena_por_significado(self):
        from chat import embeddings
        a = [1.0, 0.0, 0.0]
        self.assertAlmostEqual(embeddings.similitud(a, [1.0, 0.0, 0.0]), 1.0, places=5)
        self.assertAlmostEqual(embeddings.similitud(a, [0.0, 1.0, 0.0]), 0.0, places=5)
        self.assertGreater(embeddings.similitud(a, [0.9, 0.4, 0.0]),
                           embeddings.similitud(a, [0.3, 0.9, 0.0]))

    def test_tolera_vectores_vacios(self):
        from chat import embeddings
        self.assertEqual(embeddings.similitud(None, [1.0]), 0.0)
        self.assertEqual(embeddings.similitud([1.0], [1.0, 2.0]), 0.0)

    @override_settings(GEMINI_API_KEY="AIza-prueba", EMBEDDINGS_ACTIVOS=True)
    @patch("chat.embeddings._pedir")
    def test_la_consulta_no_reintenta_y_cae_a_lexica(self, mock_pedir):
        """Con la cuota agotada, el usuario no puede esperar 60 s por reintentos."""
        from chat import embeddings
        mock_pedir.return_value = None
        self.assertIsNone(embeddings.vector_consulta("¿cómo pido vacaciones?"))
        self.assertEqual(mock_pedir.call_args.kwargs.get("reintentos"), 0)

    @override_settings(GEMINI_API_KEY="AIza-prueba", EMBEDDINGS_ACTIVOS=True)
    @patch("chat.embeddings._pedir", return_value=None)
    def test_si_la_api_falla_la_busqueda_lexica_sigue(self, mock_pedir):
        """El requisito duro: los embeddings nunca pueden tumbar una respuesta."""
        from chat import documentos
        carpeta = tempfile.mkdtemp()
        (Path(carpeta) / "politica.txt").write_text(
            "Dias administrativos:\nCada colaborador tiene derecho a un dia "
            "administrativo por semestre, que no se acumula.\n", encoding="utf-8")
        with override_settings(DOCUMENTOS_DIR=carpeta):
            seccion = documentos.responder("dias administrativos por semestre")
        self.assertIsNotNone(seccion)


class ReevaluarTests(TestCase):
    def setUp(self):
        cache.clear()

    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_cierra_las_consultas_que_los_documentos_ya_responden(self, mocked):
        from io import StringIO
        from django.core.management import call_command
        from chat.models import ConsultaNoResuelta, registrar

        registrar("¿cuántos días administrativos tengo por semestre?", "sin_intencion")
        registrar("¿cuánto es el aguinaldo de fiestas patrias?", "sin_intencion")

        carpeta = tempfile.mkdtemp()
        (Path(carpeta) / "politica.txt").write_text(
            "Dias administrativos:\nCada colaborador tiene derecho a un dia "
            "administrativo por semestre, que no se acumula al siguiente.\n",
            encoding="utf-8")

        salida = StringIO()
        with override_settings(DOCUMENTOS_DIR=carpeta):
            call_command("reevaluar", "--aplicar", stdout=salida)

        self.assertTrue(ConsultaNoResuelta.objects
                        .get(mensaje__contains="administrativos").resuelta)
        # la que nadie documento sigue abierta: el sistema no la puede inventar
        self.assertFalse(ConsultaNoResuelta.objects
                         .get(mensaje__contains="aguinaldo").resuelta)
        self.assertIn("aguinaldo", salida.getvalue())


class SubdivisionTests(TestCase):
    """El tamaño del fragmento importa más que el algoritmo de búsqueda."""

    def setUp(self):
        cache.clear()

    def test_parte_las_secciones_largas_por_parrafo(self):
        from chat import documentos
        carpeta = tempfile.mkdtemp()
        parrafos = "\n\n".join(
            f"Parrafo {i} sobre un tema distinto con suficiente texto como para "
            f"que la seccion supere el maximo permitido por fragmento." for i in range(6)
        )
        (Path(carpeta) / "largo.txt").write_text(f"Aspectos legales:\n{parrafos}\n",
                                                 encoding="utf-8")
        with override_settings(DOCUMENTOS_DIR=carpeta):
            secciones = documentos.cargar(forzar=True)
        self.assertGreater(len(secciones), 1)
        # el título se conserva en cada trozo, para que la cita siga siendo real
        self.assertTrue(all(s["titulo"] == "Aspectos legales" for s in secciones))
        self.assertTrue(all(len(s["cuerpo"]) <= documentos.MAX_SECCION + 200
                            for s in secciones))

    def test_no_parte_las_secciones_cortas(self):
        from chat import documentos
        carpeta = tempfile.mkdtemp()
        (Path(carpeta) / "corto.txt").write_text(
            "Vigencia:\nEsta politica rige desde el 1 de julio de 2025 para toda "
            "la oficina y reemplaza cualquier version anterior del documento.\n",
            encoding="utf-8")
        with override_settings(DOCUMENTOS_DIR=carpeta):
            self.assertEqual(len(documentos.cargar(forzar=True)), 1)

    def test_no_responde_cuando_no_hay_coincidencia_real(self):
        """Citar la política equivocada es peor que decir que no se sabe."""
        from chat import documentos
        carpeta = tempfile.mkdtemp()
        (Path(carpeta) / "politica.txt").write_text(
            "Dias administrativos:\nCada colaborador tiene derecho a un dia "
            "administrativo por semestre, que no se acumula al siguiente.\n",
            encoding="utf-8")
        with override_settings(DOCUMENTOS_DIR=carpeta):
            self.assertIsNone(documentos.responder("¿quién ganó el partido de ayer?"))
            self.assertIsNone(documentos.responder("cuál es el anexo de recepción"))


@SIN_DOCUMENTOS
class CumpleanosTests(TestCase):
    """Día y mes sí; el año de nacimiento nunca sale de la capa de datos."""

    def setUp(self):
        cache.clear()

    def _preguntar(self, texto):
        return self.client.post("/api/chat/", data=json.dumps({"message": texto}),
                                content_type="application/json").json()

    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_no_expone_el_ano_de_nacimiento(self, mocked):
        from chat import buk
        personas, _ = buk.directorio()
        crudo = json.dumps(list(personas.values()), ensure_ascii=False)
        self.assertNotIn("1979", crudo)          # el año del fixture
        self.assertNotIn("birthday", crudo)
        self.assertEqual(len(list(personas.values())[0]["cumple"]), 5)  # solo MM-DD

    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_avisa_el_proximo_cuando_no_hay_ninguno_hoy(self, mocked):
        """Responder solo "nadie" no sirve: lo útil es a quién saludar pronto."""
        cuerpo = self._preguntar("¿quién está de cumpleaños hoy?")
        self.assertEqual(cuerpo["meta"]["intencion"], "cumpleanos")
        self.assertTrue(cuerpo["meta"].get("proximos"))
        self.assertIn("Nadie cumple años hoy", cuerpo["answer"])
        self.assertIn("Ana", cuerpo["answer"])   # cumple en 3 días

    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_cuenta_los_dias_desde_hoy_y_no_desde_el_rango(self, mocked):
        """Preguntando por "este mes" el día 7, uno del día 6 ya pasó."""
        from chat import buk
        gente, _ = buk.cumpleanos(HOY.replace(day=1), 30, hoy=HOY)
        for persona in gente:
            esperado = (date.fromisoformat(persona["fecha"]) - HOY).days
            self.assertEqual(persona["faltan"], esperado)

    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_el_29_de_febrero_se_celebra_el_28(self, mocked):
        from chat import buk
        with patch.dict(buk.__dict__):
            personas, _ = buk.directorio()
            list(personas.values())[0]["cumple"] = "02-29"
            gente, _ = buk.cumpleanos(date(2027, 2, 1), 28, hoy=date(2027, 2, 1))
        self.assertTrue(all(p["fecha"] != "2027-02-29" for p in gente))


@SIN_DOCUMENTOS
class TrabajandoTests(TestCase):
    def setUp(self):
        cache.clear()

    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_responde_quien_si_esta(self, mocked):
        cuerpo = self.client.post(
            "/api/chat/", data=json.dumps({"message": "¿quién está trabajando hoy?"}),
            content_type="application/json").json()
        self.assertEqual(cuerpo["meta"]["intencion"], "trabajando")
        self.assertEqual(cuerpo["meta"]["presentes"] + cuerpo["meta"]["ausentes"], 2)


class UmbralDocumentosTests(TestCase):
    """El umbral depende del corpus; la métrica no debe depender del tamaño."""

    def setUp(self):
        cache.clear()

    def _carpeta(self, cuantos):
        carpeta = tempfile.mkdtemp()
        for i in range(cuantos):
            (Path(carpeta) / f"doc{i}.txt").write_text(
                f"Tema {i}:\nContenido del tema numero {i} con texto suficiente "
                f"para que la seccion se considere valida y no se descarte.\n",
                encoding="utf-8")
        return carpeta

    def test_sin_embeddings_y_corpus_grande_no_responde(self):
        """Prefiere callarse antes que citar la sección equivocada."""
        from chat import documentos
        carpeta = self._carpeta(documentos.MAX_FRAGMENTOS_SIN_EMBEDDINGS + 5)
        with override_settings(DOCUMENTOS_DIR=carpeta, EMBEDDINGS_ACTIVOS=False):
            self.assertEqual(documentos.buscar("contenido del tema numero 3"), [])

    def test_sin_embeddings_y_corpus_chico_si_responde(self):
        from chat import documentos
        with override_settings(DOCUMENTOS_DIR=self._carpeta(5), EMBEDDINGS_ACTIVOS=False):
            self.assertTrue(documentos.buscar("contenido del tema numero 3"))


@SIN_DOCUMENTOS
class GrupoDesconocidoTests(TestCase):
    """BUK no guarda a qué cliente está asignada cada persona."""

    def setUp(self):
        cache.clear()

    def _preguntar(self, texto):
        return self.client.post("/api/chat/", data=json.dumps({"message": texto}),
                                content_type="application/json").json()

    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_avisa_en_vez_de_responder_por_toda_la_empresa(self, mocked):
        cuerpo = self._preguntar("¿quién está trabajando hoy en el equipo de Santander?")
        self.assertEqual(cuerpo["meta"]["intencion"], "grupo_desconocido")
        self.assertIn("Santander", cuerpo["answer"])
        self.assertIn("Comunicaciones", cuerpo["answer"])   # ofrece lo que sí tiene

    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_un_area_real_si_se_responde(self, mocked):
        cuerpo = self._preguntar("¿quién está trabajando hoy en el área de prensa?")
        self.assertEqual(cuerpo["meta"]["intencion"], "trabajando")


class ApodoTests(TestCase):
    """El apodo sale de BUK; el resto de custom_attributes no debe salir."""

    def setUp(self):
        cache.clear()

    def test_formato_primer_nombre_apodo_resto(self):
        self.assertEqual(
            buk.nombre_con_apodo("María José Peña Gutiérrez", "Mane"),
            'María "Mane" José Peña Gutiérrez')

    def test_no_repite_el_apodo_si_ya_esta_en_el_nombre(self):
        self.assertEqual(buk.nombre_con_apodo("Felipe Edwards Marin", "Felipe"),
                         "Felipe Edwards Marin")
        self.assertEqual(buk.nombre_con_apodo("Juan Andres Abarca Castro", "Juan Andrés"),
                         "Juan Andres Abarca Castro")

    def test_varios_apodos_en_un_campo(self):
        self.assertEqual(buk.apodos_de("Jose, JM"), ["Jose", "JM"])
        self.assertEqual(buk.apodos_de("Ali o Alice"), ["Ali", "Alice"])
        self.assertEqual(buk.apodos_de(""), [])

    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_no_filtra_el_resto_de_custom_attributes(self, mocked):
        personas, _ = buk.directorio()
        crudo = json.dumps(list(personas.values()), ensure_ascii=False)
        for reservado in ("emergencia", "994768540", "alimentaria", "Profesión"):
            self.assertNotIn(reservado, crudo)

    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_se_puede_preguntar_por_el_apodo(self, mocked):
        from chat import personas as mod
        directorio, _ = buk.directorio()
        ids, _ = mod.buscar("¿está la Mane hoy?", directorio)
        self.assertEqual(len(ids), 1)
        self.assertEqual(directorio[next(iter(ids))]["nombre"], "Ana Rojas")


class CuentasTests(TestCase):
    """La asignación por cuenta vive en la planilla; BUK la tiene vacía."""

    def setUp(self):
        cache.clear()

    def _planilla(self, filas):
        import openpyxl
        carpeta = tempfile.mkdtemp()
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Detalle Cuenta-Persona"
        ws.append(["Detalle Unipersonal"]); ws.append([]); 
        ws.append(["Cuenta / Cliente", "Persona", "Hrs. X Semana", "Rut", "Apodo"])
        for f in filas:
            ws.append(f)
        wb.save(Path(carpeta) / "cuentas.xlsx")
        return carpeta

    def test_lee_la_planilla_y_agrupa_por_cuenta(self):
        from chat import cuentas
        carpeta = self._planilla([
            ["CENCOSUD", "Rojas Ana", None, "11.111.111-1", "Ana"],
            ["CENCOSUD", "Soto Luis", None, "22.222.222-2", "Lucho"],
            ["BHP", "Rojas Ana", None, "11.111.111-1", "Ana"],
        ])
        with override_settings(DOCUMENTOS_DIR=carpeta):
            self.assertEqual(cuentas.nombres(), ["BHP", "CENCOSUD"])
            self.assertEqual(len(cuentas.buscar("vacaciones en cencosud")["ruts"]), 2)
            self.assertIsNone(cuentas.buscar("vacaciones en santander"))

    def test_prefiere_la_coincidencia_mas_larga(self):
        from chat import cuentas
        carpeta = self._planilla([
            ["AFP", "Rojas Ana", None, "11.111.111-1", None],
            ["AFP Capital", "Soto Luis", None, "22.222.222-2", None],
        ])
        with override_settings(DOCUMENTOS_DIR=carpeta):
            self.assertEqual(cuentas.buscar("gente de afp capital")["nombre"], "AFP Capital")

    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_el_rut_no_queda_en_el_directorio(self, mocked):
        carpeta = self._planilla([["CENCOSUD", "Rojas Ana", None, "11.111.111-1", "Ana"]])
        with override_settings(DOCUMENTOS_DIR=carpeta):
            personas, _ = buk.directorio()
        crudo = json.dumps(list(personas.values()), ensure_ascii=False)
        self.assertNotIn("_rut", crudo)
        self.assertNotIn("11.111.111-1", crudo)
        self.assertNotIn("111111111", crudo)

    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_un_apodo_repetido_pregunta_cual(self, mocked):
        """Hay dos "Javi" en la nómina real: elegir una al azar sería peor."""
        from chat import buk as mod
        directorio, _ = mod.directorio()
        for persona in directorio.values():
            persona["apodo"] = "Javi"
            persona["nombre_completo"] = mod.nombre_con_apodo(persona["nombre"], "Javi")
        with patch("chat.buk.directorio", return_value=(directorio, 0)):
            cuerpo = self.client.post(
                "/api/chat/", data=json.dumps({"message": "¿está la Javi hoy?"}),
                content_type="application/json").json()
        self.assertEqual(cuerpo["meta"]["intencion"], "persona_ambigua")
        self.assertIn("¿Por cuál preguntas?", cuerpo["answer"])


@SIN_DOCUMENTOS
class DesambiguacionTests(TestCase):
    """El bot pregunta cuál y la respuesta resuelve la pregunta original."""

    def setUp(self):
        cache.clear()

    def _decir(self, cliente, texto):
        return cliente.post("/api/chat/", data=json.dumps({"message": texto}),
                            content_type="application/json").json()

    def _dos_iguales(self):
        directorio, _ = buk.directorio()
        for persona in directorio.values():
            persona["apodo"] = "Javi"
            persona["nombre_completo"] = buk.nombre_con_apodo(persona["nombre"], "Javi")
        return directorio

    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_responder_con_apellido_resuelve_la_pregunta_original(self, mocked):
        cliente = self.client
        with patch("chat.buk.directorio", return_value=(self._dos_iguales(), 0)):
            primera = self._decir(cliente, "¿está la Javi hoy?")
            self.assertEqual(primera["meta"]["intencion"], "persona_ambigua")
            segunda = self._decir(cliente, "Soto")
        self.assertEqual(segunda["meta"]["intencion"], "persona")
        self.assertTrue(segunda["meta"]["desambiguado"])
        self.assertIn("Soto", segunda["answer"])

    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_responder_con_numero(self, mocked):
        cliente = self.client
        with patch("chat.buk.directorio", return_value=(self._dos_iguales(), 0)):
            self._decir(cliente, "¿está la Javi hoy?")
            segunda = self._decir(cliente, "la 1")
        self.assertEqual(segunda["meta"]["intencion"], "persona")

    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_una_respuesta_que_no_aclara_no_adivina(self, mocked):
        """Adivinar entre cuatro personas es peor que volver a preguntar."""
        cliente = self.client
        with patch("chat.buk.directorio", return_value=(self._dos_iguales(), 0)):
            self._decir(cliente, "¿está la Javi hoy?")
            segunda = self._decir(cliente, "¿y quién está de vacaciones?")
        self.assertNotEqual(segunda["meta"].get("intencion"), "persona")
        self.assertFalse(segunda["meta"].get("desambiguado"))

    def test_elegir_opcion_entiende_numero_ordinal_y_apellido(self):
        from chat.views import elegir_opcion
        opciones = ["Javiera Ignacia González Lira", "Javiera Ignacia Moreno Soza"]
        self.assertEqual(elegir_opcion("2", opciones), 1)
        self.assertEqual(elegir_opcion("la segunda", opciones), 1)
        self.assertEqual(elegir_opcion("Moreno", opciones), 1)
        self.assertEqual(elegir_opcion("González", opciones), 0)
        self.assertIsNone(elegir_opcion("no sé", opciones))
        self.assertIsNone(elegir_opcion("Javiera", opciones))   # las dos coinciden


class CuentaPorTokenTests(TestCase):
    """"El equipo de Santander" debe encontrar "BANCO SANTANDER"."""

    def setUp(self):
        cache.clear()

    def _planilla(self, nombres):
        import openpyxl
        carpeta = tempfile.mkdtemp()
        wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Detalle Cuenta-Persona"
        ws.append(["Detalle"]); ws.append([])
        ws.append(["Cuenta / Cliente", "Persona", "Hrs", "Rut", "Apodo"])
        for i, nombre in enumerate(nombres):
            ws.append([nombre, f"Persona {i}", None, f"{i}.111.111-1", None])
        wb.save(Path(carpeta) / "cuentas.xlsx")
        return carpeta

    def test_encuentra_por_una_palabra_distintiva(self):
        from chat import cuentas
        with override_settings(DOCUMENTOS_DIR=self._planilla(["BANCO SANTANDER", "CENCOSUD"])):
            self.assertEqual(cuentas.buscar("el equipo de Santander")["nombre"],
                             "BANCO SANTANDER")

    def test_si_la_palabra_es_de_varias_cuentas_pregunta(self):
        from chat import cuentas
        with override_settings(DOCUMENTOS_DIR=self._planilla(["AFP Capital", "AFP Cuprum"])):
            resultado = cuentas.buscar("gente de AFP")
            self.assertEqual(resultado["ambiguas"], ["AFP Capital", "AFP Cuprum"])
            self.assertEqual(cuentas.buscar("gente de AFP Cuprum")["nombre"], "AFP Cuprum")

    def test_las_palabras_genericas_no_identifican(self):
        from chat import cuentas
        with override_settings(DOCUMENTOS_DIR=self._planilla(["BANCO SANTANDER", "BANCO ESTADO"])):
            self.assertIsNone(cuentas.buscar("el equipo del banco"))


@SIN_DOCUMENTOS
class DisponiblesTests(TestCase):
    """"Disponible" es lo contrario de ausente; antes se leía al revés."""

    def setUp(self):
        cache.clear()

    def _preguntar(self, texto):
        return self.client.post("/api/chat/", data=json.dumps({"message": texto}),
                                content_type="application/json").json()

    def test_disponible_no_es_una_palabra_de_ausencia(self):
        for pregunta in ("que ejecutivos estan disponibles hoy",
                         "¿quién está disponible hoy?",
                         "quienes estan disponibles en cencosud"):
            self.assertEqual(intents.interpretar(pregunta, HOY)["intencion"],
                             "trabajando", pregunta)

    def test_ausente_sigue_siendo_ausencia(self):
        for pregunta in ("¿quién está fuera hoy?", "¿quién está ausente?"):
            self.assertEqual(intents.interpretar(pregunta, HOY)["intencion"],
                             "ausencias", pregunta)

    def test_detecta_la_familia_de_cargo_en_singular_y_plural(self):
        familias = {"Ejecutivos", "Directores", "Consultores Senior"}
        self.assertEqual(intents.detectar_familia("que ejecutivos hay", familias),
                         "Ejecutivos")
        self.assertEqual(intents.detectar_familia("los director de area", familias),
                         "Directores")
        self.assertIsNone(intents.detectar_familia("quien esta fuera", familias))

    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_lista_los_nombres_cuando_el_grupo_es_acotado(self, mocked):
        """Preguntar "qué ejecutivos" y recibir solo un número no responde."""
        cuerpo = self._preguntar("¿qué analistas están disponibles hoy?")
        self.assertEqual(cuerpo["meta"]["intencion"], "trabajando")

    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_cuenta_personas_y_no_registros(self, mocked):
        """Quien parte sus vacaciones en tramos es una persona, no tres."""
        cuerpo = self._preguntar("¿quién está fuera este mes?")
        claves = [(i["id"], i["tipo"]) for i in cuerpo["items"]]
        self.assertEqual(len(claves), len(set(claves)))  # sin filas repetidas
        self.assertIn(str(len({i["id"] for i in cuerpo["items"]})), cuerpo["answer"])


@SIN_DOCUMENTOS
class NombrePrioritarioTests(TestCase):
    """Un nombre es más específico que el verbo de la pregunta."""

    def setUp(self):
        cache.clear()

    def _preguntar(self, texto, cliente=None):
        return (cliente or self.client).post(
            "/api/chat/", data=json.dumps({"message": texto}),
            content_type="application/json").json()

    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_el_nombre_manda_sobre_la_intencion_de_grupo(self, mocked):
        """"¿Ana está disponible?" pregunta por Ana, no por la nómina."""
        cuerpo = self._preguntar("¿Ana Rojas está disponible?")
        self.assertEqual(cuerpo["meta"]["intencion"], "persona")
        self.assertIn("Ana", cuerpo["answer"])

    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_sin_nombre_sigue_respondiendo_por_el_grupo(self, mocked):
        cuerpo = self._preguntar("¿quién está disponible hoy?")
        self.assertEqual(cuerpo["meta"]["intencion"], "trabajando")

    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_un_saludo_no_consulta_el_directorio(self, mocked):
        """La cortesía va antes de buscar personas."""
        cuerpo = self._preguntar("hola")
        self.assertEqual(cuerpo["meta"]["intencion"], "cortesia")
        mocked.assert_not_called()


@SIN_DOCUMENTOS
class QuisoDecirTests(TestCase):
    """Apellido mal escrito: sugerir en vez de listar a todos los homónimos."""

    def setUp(self):
        cache.clear()

    def _preguntar(self, texto, cliente=None):
        return (cliente or self.client).post(
            "/api/chat/", data=json.dumps({"message": texto}),
            content_type="application/json").json()

    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_sugiere_el_nombre_parecido(self, mocked):
        # "Rojs" sola no coincide con nadie, pero se parece a "Rojas"
        cuerpo = self._preguntar("¿Rojs está disponible?")
        self.assertEqual(cuerpo["meta"]["intencion"], "quiso_decir")
        self.assertIn("¿Querrás decir", cuerpo["answer"])
        self.assertIn("Ana", cuerpo["answer"])

    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_confirmar_con_si_responde_la_pregunta_original(self, mocked):
        cliente = self.client
        self._preguntar("¿Rojs está disponible?", cliente)
        segunda = self._preguntar("sí", cliente)
        self.assertEqual(segunda["meta"]["intencion"], "persona")
        self.assertTrue(segunda["meta"]["desambiguado"])

    def test_no_sugiere_por_cualquier_palabra(self):
        """"pinilla" no debe sugerir "Padilla" solo porque comparten letras."""
        from chat import personas as mod
        directorio = {1: {"id": 1, "nombre": "Reimar Padilla Castellano", "apodo": ""}}
        self.assertEqual(mod.sugerir("felipe pinilla esta disponible", directorio), [])
        self.assertEqual(mod.sugerir("reimar padila esta disponible", directorio), [1])

    def test_si_confirma_solo_cuando_hay_una_opcion(self):
        from chat.views import elegir_opcion
        self.assertEqual(elegir_opcion("sí", ["Ana Rojas"]), 0)
        self.assertIsNone(elegir_opcion("sí", ["Ana Rojas", "Ana Soto"]))

    def test_el_vocabulario_de_preguntas_no_sugiere_nombres(self):
        """"años" se parece a "Llanos": sin esto, "¿quién cumple años?"
        terminaba respondiendo por una persona."""
        from chat import personas as mod
        directorio = {1: {"id": 1, "nombre": "Bastian Nicolas Llanos Guijuelos",
                          "apodo": "Bastián"}}
        self.assertEqual(mod.sugerir("quien cumple anos este mes", directorio), [])
        self.assertEqual(mod.sugerir("bastian llanoz esta hoy", directorio), [1])


@SIN_DOCUMENTOS
class PertenenciaTests(TestCase):
    """Quién compone un equipo es otra pregunta que quién está disponible."""

    def setUp(self):
        cache.clear()

    def _planilla(self):
        import openpyxl
        carpeta = tempfile.mkdtemp()
        wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Detalle Cuenta-Persona"
        ws.append(["Detalle"]); ws.append([])
        ws.append(["Cuenta / Cliente", "Persona", "Hrs", "Rut", "Apodo"])
        ws.append(["CENCOSUD", "Rojas Ana", None, "11.111.111-1", "Mane"])
        ws.append(["BANCO SANTANDER", "Soto Luis", None, "22.222.222-2", "Lucho"])
        wb.save(Path(carpeta) / "cuentas.xlsx")
        return carpeta

    def _preguntar(self, texto, cliente=None):
        return (cliente or self.client).post(
            "/api/chat/", data=json.dumps({"message": texto}),
            content_type="application/json").json()

    def test_se_distingue_de_la_disponibilidad(self):
        self.assertEqual(
            intents.interpretar("quienes estan en el equipo de Cencosud", HOY)["intencion"],
            "pertenencia")
        self.assertEqual(
            intents.interpretar("quien esta trabajando hoy en Cencosud", HOY)["intencion"],
            "trabajando")

    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_lista_a_los_integrantes(self, mocked):
        with override_settings(DOCUMENTOS_DIR=self._planilla()):
            cuerpo = self._preguntar("¿quiénes están en el equipo de CENCOSUD?")
        self.assertEqual(cuerpo["meta"]["intencion"], "pertenencia")
        self.assertEqual(len(cuerpo["items"]), 1)
        self.assertIn("Ana", cuerpo["items"][0]["nombre"])

    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_por_una_persona_responde_si_o_no(self, mocked):
        """"¿X está en el equipo de Y?" no pregunta si está hoy."""
        with override_settings(DOCUMENTOS_DIR=self._planilla()):
            si = self._preguntar("¿Ana está en el equipo de CENCOSUD?")
            no = self._preguntar("¿Ana está en el equipo de BANCO SANTANDER?")
        self.assertTrue(si["answer"].startswith("Sí,"))
        self.assertTrue(no["answer"].startswith("No,"))
        self.assertIn("CENCOSUD", no["answer"])   # dice dónde sí está

    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_una_pregunta_corta_hereda_el_equipo_anterior(self, mocked):
        cliente = self.client
        with override_settings(DOCUMENTOS_DIR=self._planilla()):
            self._preguntar("¿quiénes están en el equipo de CENCOSUD?", cliente)
            segunda = self._preguntar("están disponibles", cliente)
        self.assertEqual(segunda["meta"]["grupo_heredado"], "CENCOSUD")

    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_no_hereda_si_la_pregunta_nombra_otro_equipo(self, mocked):
        cliente = self.client
        with override_settings(DOCUMENTOS_DIR=self._planilla()):
            self._preguntar("¿quiénes están en el equipo de CENCOSUD?", cliente)
            segunda = self._preguntar("¿quién está disponible en BANCO SANTANDER?", cliente)
        self.assertIsNone(segunda["meta"].get("grupo_heredado"))

    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_una_pregunta_completa_no_hereda(self, mocked):
        """El bug: la conversación se quedaba pegada a un cliente."""
        cliente = self.client
        with override_settings(DOCUMENTOS_DIR=self._planilla()):
            self._preguntar("¿quién está de vacaciones en CENCOSUD?", cliente)
            segunda = self._preguntar("necesito saber quien esta de vacaciones", cliente)
        self.assertIsNone(segunda["meta"].get("grupo_heredado"))

    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_en_general_sale_del_equipo_y_lo_olvida(self, mocked):
        cliente = self.client
        with override_settings(DOCUMENTOS_DIR=self._planilla()):
            self._preguntar("¿quién está de vacaciones en CENCOSUD?", cliente)
            self._preguntar("quien esta de vacaciones en general", cliente)
            tercera = self._preguntar("están disponibles", cliente)
        self.assertIsNone(tercera["meta"].get("grupo_heredado"))

    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_recargar_la_pagina_limpia_el_contexto(self, mocked):
        cliente = self.client
        with override_settings(DOCUMENTOS_DIR=self._planilla()):
            self._preguntar("¿quiénes están en el equipo de CENCOSUD?", cliente)
            cliente.get("/api/status/")          # lo llama la página al cargar
            segunda = self._preguntar("están disponibles", cliente)
        self.assertIsNone(segunda["meta"].get("grupo_heredado"))

    def test_distingue_fragmento_de_pregunta_completa(self):
        from chat.views import _es_continuacion
        for fragmento in ("están disponibles", "y ahora?", "y mañana"):
            self.assertTrue(_es_continuacion(fragmento), fragmento)
        for completa in ("necesito saber quien esta de vacaciones",
                         "quien esta de vacaciones en general",
                         "cuantas personas hay activas",
                         "todos"):
            self.assertFalse(_es_continuacion(completa), completa)


class InfoPersonaTests(TestCase):
    """`info_persona`: la herramienta que cubre "quien es X" / "que cuentas
    maneja X" con una sola llamada, en vez de una por cada forma de decirlo.
    """

    def setUp(self):
        cache.clear()

    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_devuelve_identidad_y_no_ausencias(self, mocked):
        from chat import herramientas
        resultado = herramientas.info_persona("Ana Rojas")
        self.assertTrue(resultado["encontrada"])
        self.assertEqual(resultado["nombre"], "Ana Rojas")
        self.assertIn("cargo", resultado)
        self.assertIn("cuentas", resultado)
        self.assertNotIn("ausencias", resultado)  # esa es otra herramienta

    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_nombre_desconocido_no_encuentra(self, mocked):
        from chat import herramientas
        resultado = herramientas.info_persona("Nadie Existe")
        self.assertFalse(resultado["encontrada"])


@override_settings(GEMINI_API_KEY="AIza-prueba",
                   ASISTENTE_ANONIMIZAR=False)
class MemoriaConversacionTests(TestCase):
    """El modo "todo por el modelo" necesita acordarse de lo ya hablado para
    entender un seguimiento como "y esta disponible hoy?"."""

    @patch("chat.asistente._cliente_gemini")
    def test_el_historial_previo_viaja_en_la_siguiente_llamada(self, mock_cliente):
        from chat import asistente
        generar = mock_cliente.return_value.models.generate_content
        generar.return_value = _respuesta_gemini(texto="Si, esta en su jornada.")
        historial_previo = [
            {"role": "user", "texto": "quien es Ana Rojas?"},
            {"role": "model", "texto": "Ana Rojas es Analista de Cencosud."},
        ]

        texto, meta = asistente.responder(
            "y esta disponible hoy?", HOY, historial=historial_previo)

        enviado = str(generar.call_args.kwargs["contents"])
        self.assertIn("Ana Rojas es Analista de Cencosud", enviado)
        self.assertEqual(meta["historial"][-2]["texto"], "y esta disponible hoy?")
        self.assertEqual(meta["historial"][-1]["texto"], texto)

    @patch("chat.asistente._cliente_gemini")
    def test_el_alias_se_mantiene_entre_turnos(self, mock_cliente):
        """Con anonimizacion activa, la misma persona no cambia de seudonimo
        a mitad de conversacion."""
        from chat import asistente
        generar = mock_cliente.return_value.models.generate_content
        generar.return_value = _respuesta_gemini(texto="Sigue igual.")
        alias_previo = {"Ana Rojas": "Persona 1"}

        with override_settings(ASISTENTE_ANONIMIZAR=True):
            asistente.responder("y ahora?", HOY, alias=alias_previo)

        self.assertEqual(alias_previo, {"Ana Rojas": "Persona 1"})


class EquipoDeTests(TestCase):
    """`equipo_de`: la pregunta inversa a info_persona ("quien es del equipo
    de Y", "muestrame el equipo que atiende Y")."""

    def setUp(self):
        cache.clear()

    def _planilla(self):
        import openpyxl
        carpeta = tempfile.mkdtemp()
        libro = openpyxl.Workbook()
        hoja = libro.active
        hoja.title = "Detalle Cuenta-Persona"
        for _ in range(3):
            hoja.append([])
        hoja.append(["BANCO SANTANDER", "Ana Rojas", None, "11.111.111-1"])
        libro.save(Path(carpeta) / "cuentas.xlsx")
        return carpeta

    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_equipo_de_una_cuenta(self, mocked):
        from chat import herramientas
        with override_settings(DOCUMENTOS_DIR=self._planilla()):
            resultado = herramientas.equipo_de("Santander")
        self.assertTrue(resultado["encontrado"])
        self.assertEqual(resultado["grupo"], "BANCO SANTANDER")
        self.assertIn("Ana Rojas", [p["nombre"] for p in resultado["personas"]])

    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_equipo_de_un_area(self, mocked):
        from chat import herramientas
        directorio, _ = buk.directorio()
        area = next(p["area"] for p in directorio.values() if p.get("area"))
        resultado = herramientas.equipo_de(area)
        self.assertTrue(resultado["encontrado"])
        self.assertGreaterEqual(resultado["total"], 1)

    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_grupo_inexistente_no_encuentra(self, mocked):
        from chat import herramientas
        resultado = herramientas.equipo_de("no existe este grupo")
        self.assertFalse(resultado["encontrado"])


class PoliticaVacacionesDocTests(TestCase):
    """El documento real debe responder, por texto solo (sin embeddings), a
    las preguntas frecuentes de vacaciones. Si esto se rompe, alguien cambio
    los titulos del documento o los umbrales de busqueda."""

    def setUp(self):
        cache.clear()

    def _con_el_documento_real(self):
        origen = Path(settings.BASE_DIR) / "datos" / "politica_vacaciones.md"
        carpeta = tempfile.mkdtemp()
        (Path(carpeta) / "politica_vacaciones.md").write_text(
            origen.read_text(encoding="utf-8"), encoding="utf-8")
        return override_settings(DOCUMENTOS_DIR=carpeta, EMBEDDINGS_ACTIVOS=False)

    def test_preguntas_frecuentes_encuentran_seccion(self):
        """El modelo pide 3 secciones (buscar_politica), no solo la primera:
        alcanza con que el titulo correcto este entre esas 3.
        """
        from chat import documentos
        preguntas_y_titulo = [
            ("cuantos dias de vacaciones me corresponden al ano",
             "Cuántos días de vacaciones corresponden"),
            ("puedo fraccionar mis vacaciones o debo tomar los 15 dias seguidos",
             "Cuántos días de vacaciones corresponden"),
            ("que pasa si me enfermo mientras estoy de vacaciones",
             "Qué pasa si me enfermo estando de vacaciones"),
            ("con cuanta anticipacion debo pedir mis vacaciones",
             "Cómo y con cuánta anticipación se piden las vacaciones"),
            ("como funciona el incentivo de dias adicionales por menor demanda",
             "Incentivo de días adicionales por menor demanda"),
            ("soy de asuntos publicos, cuales son mis meses de menor demanda",
             "Incentivo de días adicionales por menor demanda"),
            ("tengo derecho a teletrabajar en vacaciones escolares de mis hijos",
             "Teletrabajo por conciliación de la vida laboral, familiar y personal"),
            ("cuantos dias administrativos tengo al ano", "Días administrativos"),
            ("puedo elegir si trabajo el 24 o el 31 de diciembre",
             "Feriado especial de Navidad o Año Nuevo"),
        ]
        with self._con_el_documento_real():
            for pregunta, titulo in preguntas_y_titulo:
                encontradas = documentos.buscar(pregunta, cuantas=3)
                titulos = [s["titulo"] for s in encontradas]
                self.assertIn(titulo, titulos, f"{pregunta!r} -> {titulos}")

    def test_no_quedan_datos_personales_de_la_firma(self):
        """El .txt original traia el email y RUT de quien firmo el documento:
        no deben terminar citables en una respuesta del bot."""
        origen = Path(settings.BASE_DIR) / "datos" / "politica_vacaciones.md"
        contenido = origen.read_text(encoding="utf-8").lower()
        self.assertNotIn("@gmail.com", contenido)
        self.assertNotIn("12.454.685-0", contenido)


class MarcaNoSeTests(TestCase):
    """Si el modelo no puede responder, tiene que decirlo con la marca NO_SE,
    no con una frase cualquiera que se confunda con una respuesta real."""

    def test_separa_la_marca_y_el_texto(self):
        from chat.asistente import _separar_exito
        texto, exitosa = _separar_exito("NO_SE: No tengo esa información.")
        self.assertFalse(exitosa)
        self.assertEqual(texto, "No tengo esa información.")

    def test_sin_marca_es_exito(self):
        from chat.asistente import _separar_exito
        texto, exitosa = _separar_exito("Ana está en Cencosud.")
        self.assertTrue(exitosa)
        self.assertEqual(texto, "Ana está en Cencosud.")

    def test_la_marca_no_distingue_mayusculas(self):
        from chat.asistente import _separar_exito
        _, exitosa = _separar_exito("no_se: no tengo ese dato.")
        self.assertFalse(exitosa)


@override_settings(GEMINI_API_KEY="AIza-prueba",
                   ASISTENTE_SIEMPRE=True)
class RespuestaNoExitosaTests(TestCase):
    """Cuando el modelo se rinde, la pregunta queda registrada como 'sin
    datos' y el usuario nunca ve el texto crudo de la marca."""

    def setUp(self):
        cache.clear()

    @patch("chat.buk.requests.get", side_effect=fake_get)
    @patch("chat.asistente._cliente_gemini")
    def test_se_registra_como_sin_datos_y_no_se_muestra_la_marca(self, mock_cliente, mock_buk):
        from chat.models import ConsultaNoResuelta
        mock_cliente.return_value.models.generate_content.return_value = _respuesta_gemini(
            texto="NO_SE: No tengo acceso a esa información.")

        cuerpo = self.client.post(
            "/api/chat/", data='{"message":"cuanto gano yo el mes pasado?"}',
            content_type="application/json",
        ).json()

        self.assertNotIn("NO_SE", cuerpo["answer"])
        self.assertNotEqual(cuerpo["meta"]["intencion"], "modelo")
        fila = ConsultaNoResuelta.objects.get()
        self.assertEqual(fila.motivo, "sin_datos")
        self.assertEqual(fila.veces, 1)  # no se registra dos veces la misma
        # el modelo ya dijo que no sabe: no vale la pena volver a llamarlo
        self.assertEqual(mock_cliente.return_value.models.generate_content.call_count, 1)

    @patch("chat.buk.requests.get", side_effect=fake_get)
    @patch("chat.asistente._cliente_gemini")
    def test_si_pudo_responder_no_se_registra(self, mock_cliente, mock_buk):
        from chat.models import ConsultaNoResuelta
        mock_cliente.return_value.models.generate_content.return_value = _respuesta_gemini(
            texto="Todo bien por aquí.")

        self.client.post("/api/chat/", data='{"message":"como estas?"}',
                         content_type="application/json")

        self.assertEqual(ConsultaNoResuelta.objects.count(), 0)


class FeedbackTests(TestCase):
    """El boton de pulgar abajo bajo cada respuesta."""

    def setUp(self):
        cache.clear()

    def test_marcar_como_no_exitosa_la_registra(self):
        from chat.models import ConsultaNoResuelta
        respuesta = self.client.post(
            "/api/feedback/",
            data=json.dumps({"message": "quien es Ana Rojas?", "exitosa": False}),
            content_type="application/json",
        )
        self.assertEqual(respuesta.status_code, 200)
        fila = ConsultaNoResuelta.objects.get()
        self.assertEqual(fila.motivo, "marcada_no_exitosa")
        self.assertEqual(fila.mensaje, "quien es Ana Rojas?")

    def test_marcar_como_exitosa_no_registra_nada(self):
        from chat.models import ConsultaNoResuelta
        self.client.post(
            "/api/feedback/",
            data=json.dumps({"message": "quien es Ana Rojas?", "exitosa": True}),
            content_type="application/json",
        )
        self.assertEqual(ConsultaNoResuelta.objects.count(), 0)

    def test_sin_mensaje_responde_error(self):
        respuesta = self.client.post(
            "/api/feedback/", data=json.dumps({"exitosa": False}),
            content_type="application/json",
        )
        self.assertEqual(respuesta.status_code, 400)


class IdentidadYCumplePersonaTests(TestCase):
    """Nombrar a alguien no siempre pregunta por su disponibilidad: "quien es
    X", "que cargo tiene X" y "cuando cumple anos X" son otras tres preguntas,
    y tienen que responderse aunque el modelo no este disponible (esto corre
    con las reglas, sin mockear Gemini)."""

    def setUp(self):
        cache.clear()

    def _planilla_con_cuenta(self):
        import openpyxl
        carpeta = tempfile.mkdtemp()
        libro = openpyxl.Workbook()
        hoja = libro.active
        hoja.title = "Detalle Cuenta-Persona"
        for _ in range(3):
            hoja.append([])
        hoja.append(["BANCO SANTANDER", "Ana Rojas", None, "11.111.111-1"])
        libro.save(Path(carpeta) / "cuentas.xlsx")
        return carpeta

    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_quien_es_da_identidad_no_ausencias(self, mocked):
        cuerpo = self.client.post(
            "/api/chat/", data='{"message":"quien es Ana Rojas?"}',
            content_type="application/json",
        ).json()
        self.assertEqual(cuerpo["meta"]["intencion"], "identidad_persona")
        self.assertIn("Analista", cuerpo["answer"])
        self.assertNotIn("licencia", cuerpo["answer"].lower())

    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_que_cargo_tiene_tambien_es_identidad(self, mocked):
        cuerpo = self.client.post(
            "/api/chat/", data='{"message":"que cargo tiene Ana Rojas?"}',
            content_type="application/json",
        ).json()
        self.assertEqual(cuerpo["meta"]["intencion"], "identidad_persona")

    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_que_cuentas_maneja_lista_las_cuentas(self, mocked):
        with override_settings(DOCUMENTOS_DIR=self._planilla_con_cuenta()):
            cuerpo = self.client.post(
                "/api/chat/", data='{"message":"que cuentas maneja Ana Rojas?"}',
                content_type="application/json",
            ).json()
        self.assertEqual(cuerpo["meta"]["intencion"], "identidad_persona")
        self.assertIn("BANCO SANTANDER", cuerpo["answer"])

    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_sin_cuentas_asignadas_lo_dice(self, mocked):
        cuerpo = self.client.post(
            "/api/chat/", data='{"message":"que clientes atiende Ana Rojas?"}',
            content_type="application/json",
        ).json()
        self.assertIn("No tiene cuentas", cuerpo["answer"])

    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_cuando_cumple_anos_no_es_ausencias(self, mocked):
        cuerpo = self.client.post(
            "/api/chat/", data='{"message":"cuando cumple años Ana Rojas?"}',
            content_type="application/json",
        ).json()
        self.assertEqual(cuerpo["meta"]["intencion"], "cumpleanos_persona")
        self.assertNotIn("licencia", cuerpo["answer"].lower())
        self.assertNotIn("jornada", cuerpo["answer"].lower())

    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_sin_ese_dato_no_ausencias(self, mocked):
        """Luis Soto no tiene apodo raro, pero el cumpleanos si esta seteado;
        si faltara, avisa en vez de caer a ausencias."""
        from unittest.mock import patch as p
        with p("chat.buk._cumple", return_value=""):
            cuerpo = self.client.post(
                "/api/chat/", data='{"message":"cuando cumple años Ana Rojas?"}',
                content_type="application/json",
            ).json()
        self.assertIn("No tengo registrada", cuerpo["answer"])


class PersonaPorCargoTests(TestCase):
    """"Quien es el gerente de X" no nombra a nadie: hay que buscar por el
    texto del cargo."""

    def setUp(self):
        cache.clear()

    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_encuentra_por_cargo_exacto(self, mocked):
        cuerpo = self.client.post(
            "/api/chat/", data='{"message":"quien es la analista?"}',
            content_type="application/json",
        ).json()
        self.assertEqual(cuerpo["meta"]["intencion"], "identidad_persona")
        self.assertIn("Ana", cuerpo["answer"])
        self.assertIn("Analista", cuerpo["answer"])

    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_cargo_inexistente_no_inventa(self, mocked):
        cuerpo = self.client.post(
            "/api/chat/", data='{"message":"quien es el gerente de finanzas?"}',
            content_type="application/json",
        ).json()
        self.assertNotEqual(cuerpo["meta"]["intencion"], "identidad_persona")

    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_herramienta_persona_por_cargo(self, mocked):
        from chat import herramientas
        resultado = herramientas.persona_por_cargo("analista")
        self.assertTrue(resultado["encontrada"])
        self.assertEqual(resultado["nombre"], "Ana Rojas")


class EstadoAsistenteTests(TestCase):
    """El indicador de modelo en la interfaz: que esta activo y, si no,
    por que."""

    def setUp(self):
        cache.clear()

    def test_sin_clave_dice_no_configurado(self):
        from chat import asistente
        with override_settings(GEMINI_API_KEY=""):
            estado = asistente.estado()
        self.assertFalse(estado["disponible"])
        self.assertEqual(estado["motivo"], "sin_clave")

    @override_settings(GEMINI_API_KEY="AIza-prueba")
    def test_con_clave_y_sin_fallas_esta_disponible(self):
        from chat import asistente
        estado = asistente.estado()
        self.assertTrue(estado["disponible"])
        self.assertIsNone(estado["motivo"])
        self.assertEqual(estado["modelo"], settings.GEMINI_MODEL)

    @override_settings(GEMINI_API_KEY="AIza-prueba", ASISTENTE_FALLAS_MAX=1)
    def test_cuota_agotada_se_distingue_de_clave_invalida(self):
        from chat import asistente
        asistente.registrar_falla(RuntimeError("429 RESOURCE_EXHAUSTED"))
        estado = asistente.estado()
        self.assertFalse(estado["disponible"])
        self.assertEqual(estado["motivo"], "cuota_agotada")
        self.assertIn("cuota", estado["motivo_legible"])

    @override_settings(GEMINI_API_KEY="AIza-prueba", ASISTENTE_FALLAS_MAX=1)
    def test_prepago_agotado_se_distingue_de_cuota_gratis(self):
        """Con facturacion activa, un 429 significa saldo prepagado en cero,
        no el limite de 20/dia del free tier: el aviso tiene que ser otro."""
        from chat import asistente
        asistente.registrar_falla(RuntimeError(
            "429 RESOURCE_EXHAUSTED. Your prepayment credits are depleted. "
            "Please go to AI Studio to manage your billing."))
        estado = asistente.estado()
        self.assertEqual(estado["motivo"], "prepago_agotado")
        self.assertIn("prepagados", estado["motivo_legible"])

    @override_settings(GEMINI_API_KEY="AIza-prueba", ASISTENTE_FALLAS_MAX=1)
    def test_clave_invalida_se_distingue_de_cuota(self):
        from chat import asistente
        asistente.registrar_falla(RuntimeError("403 PERMISSION_DENIED"))
        estado = asistente.estado()
        self.assertEqual(estado["motivo"], "clave_invalida")

    @override_settings(GEMINI_API_KEY="AIza-prueba", ASISTENTE_FALLAS_MAX=1)
    def test_un_exito_borra_el_motivo_guardado(self):
        from chat import asistente
        asistente.registrar_falla(RuntimeError("429"))
        asistente.registrar_exito()
        estado = asistente.estado()
        self.assertTrue(estado["disponible"])

    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_api_status_incluye_el_estado_del_asistente(self, mocked):
        cuerpo = self.client.get("/api/status/").json()
        self.assertIn("asistente", cuerpo)
        self.assertIn("disponible", cuerpo["asistente"])
        self.assertIn("modelo", cuerpo["asistente"])
