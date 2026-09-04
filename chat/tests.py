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

EMPLEADOS = {
    "pagination": {"next": None},
    "data": [
        {"id": 335, "full_name": "Ana Rojas", "rut": "11.111.111-1",
         "current_job": {"role": {"name": "Analista"}, "boss": {"rut": "22.222.222-2"}}},
        {"id": 468, "full_name": "Luis Soto", "email": "luis@azerta.cl",
         "current_job": {"role": {"name": "Disenador"}}},
    ],
}

# Las fechas se calculan desde hoy: fijarlas hace que la suite empiece a fallar
# sola cuando cambia el dia, que es justo lo que paso.
HOY = date.today()
DIA = timedelta(days=1)
_f = lambda dias: (HOY + dias * DIA).isoformat()

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


def fake_get(url, **kwargs):
    from unittest.mock import Mock
    if "/vacations" in url:
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
        self.assertIn("Ana Rojas", nombres)     # 24-ago -> 4-sep, en curso
        self.assertIn("Luis Soto", nombres)     # dia administrativo de hoy
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
        self.assertEqual(len(cuerpo["items"]), 3)  # 2 vacaciones + 1 licencia

    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_subtipo_administrativo(self, mocked):
        cuerpo = self._preguntar("dias administrativos hoy")
        self.assertEqual(len(cuerpo["items"]), 1)
        self.assertEqual(cuerpo["items"][0]["nombre"], "Luis Soto")
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
        self.assertIn("Ana Rojas", cuerpo["answer"])
        self.assertNotIn("Luis Soto", cuerpo["answer"])  # no lista a los demas
        self.assertEqual(len(cuerpo["items"]), 1)

    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_persona_sin_ausencias_lo_dice(self, mocked):
        cuerpo = self.client.post(
            "/api/chat/", data='{"message":"esta Ana Rojas de vacaciones el 2026-11-11?"}',
            content_type="application/json",
        ).json()
        self.assertIn("no registra ausencias", cuerpo["answer"])
        self.assertIn("Ana Rojas", cuerpo["answer"])


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


class _Llamada:
    def __init__(self, nombre, argumentos, id_="call_1"):
        self.id = id_
        self.function = Mock(name=nombre, arguments=json.dumps(argumentos))
        self.function.name = nombre


class _Mensaje:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []

    def model_dump(self, **kwargs):
        return {"role": "assistant", "content": self.content}


def _respuesta(mensaje):
    return Mock(choices=[Mock(message=mensaje)])


@SIN_DOCUMENTOS
class AsistenteTests(TestCase):
    """El modelo se simula: la suite no gasta tokens ni necesita clave."""

    def setUp(self):
        cache.clear()

    @override_settings(ASISTENTE_PROVEEDOR="openai", OPENAI_API_KEY="")
    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_sin_clave_la_app_sigue_funcionando(self, mock_buk):
        cuerpo = self.client.post(
            "/api/chat/", data='{"message":"cuanto es el aguinaldo?"}',
            content_type="application/json",
        ).json()
        self.assertEqual(cuerpo["meta"]["intencion"], "sin_datos")

    @override_settings(ASISTENTE_PROVEEDOR="openai", OPENAI_API_KEY="sk-prueba", ASISTENTE_ANONIMIZAR=False)
    @patch("chat.buk.requests.get", side_effect=fake_get)
    @patch("chat.asistente._cliente_openai")
    def test_usa_las_herramientas_y_responde(self, mock_cliente, mock_buk):
        crear = mock_cliente.return_value.chat.completions.create
        crear.side_effect = [
            _respuesta(_Mensaje(tool_calls=[
                _Llamada("listar_ausencias",
                         {"desde": _f(0), "hasta": _f(0)})])),
            _respuesta(_Mensaje(content="Hay 3 personas fuera hoy.")),
        ]
        cuerpo = self.client.post(
            "/api/chat/", data='{"message":"hazme un resumen de la carga del equipo"}',
            content_type="application/json",
        ).json()
        self.assertEqual(cuerpo["meta"]["intencion"], "modelo")
        self.assertEqual(cuerpo["meta"]["herramientas"], ["listar_ausencias"])
        self.assertIn("3 personas", cuerpo["answer"])

    @override_settings(ASISTENTE_PROVEEDOR="openai", OPENAI_API_KEY="sk-prueba", ASISTENTE_ANONIMIZAR=False)
    @patch("chat.buk.requests.get", side_effect=fake_get)
    @patch("chat.asistente._cliente_openai")
    def test_la_herramienta_no_entrega_el_motivo_de_la_licencia(self, mock_cliente, mock_buk):
        from chat import herramientas
        datos = herramientas.listar_ausencias(_f(0), _f(0))
        crudo = json.dumps(datos, ensure_ascii=False)
        for reservado in ("accidente_comun", "accidente comun", "licence_type", "post natal"):
            self.assertNotIn(reservado, crudo)

    @override_settings(ASISTENTE_PROVEEDOR="openai", OPENAI_API_KEY="sk-prueba", ASISTENTE_ANONIMIZAR=True)
    @patch("chat.buk.requests.get", side_effect=fake_get)
    @patch("chat.asistente._cliente_openai")
    def test_anonimiza_la_nomina_y_restituye_los_nombres(self, mock_cliente, mock_buk):
        crear = mock_cliente.return_value.chat.completions.create
        crear.side_effect = [
            _respuesta(_Mensaje(tool_calls=[
                _Llamada("listar_ausencias",
                         {"desde": _f(0), "hasta": _f(0)})])),
            _respuesta(_Mensaje(content="Persona 1 esta de vacaciones.")),
        ]
        cuerpo = self.client.post(
            "/api/chat/", data='{"message":"hazme un resumen de la carga del equipo"}',
            content_type="application/json",
        ).json()
        # lo que viajo al modelo no lleva nombres reales
        enviado = json.dumps(crear.call_args_list[1].kwargs["messages"], ensure_ascii=False)
        self.assertNotIn("Ana Rojas", enviado)
        self.assertIn("Persona 1", enviado)
        # pero el usuario ve el nombre real
        self.assertIn("Ana Rojas", cuerpo["answer"])

    @override_settings(ASISTENTE_PROVEEDOR="openai", OPENAI_API_KEY="sk-prueba")
    @patch("chat.buk.requests.get", side_effect=fake_get)
    @patch("chat.asistente._cliente_openai")
    def test_si_el_modelo_falla_la_app_responde_igual(self, mock_cliente, mock_buk):
        mock_cliente.return_value.chat.completions.create.side_effect = RuntimeError("503")
        cuerpo = self.client.post(
            "/api/chat/", data='{"message":"cuanto es el aguinaldo?"}',
            content_type="application/json",
        ).json()
        self.assertEqual(cuerpo["meta"]["intencion"], "sin_datos")

    @override_settings(ASISTENTE_PROVEEDOR="openai", OPENAI_API_KEY="sk-prueba")
    @patch("chat.buk.requests.get", side_effect=fake_get)
    @patch("chat.asistente._cliente_openai")
    def test_las_reglas_responden_sin_llamar_al_modelo(self, mock_cliente, mock_buk):
        """Lo que el router ya entiende no debe gastar tokens."""
        self.client.post("/api/chat/", data='{"message":"quien esta fuera hoy"}',
                         content_type="application/json")
        mock_cliente.assert_not_called()


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

    @override_settings(ASISTENTE_PROVEEDOR="openai", OPENAI_API_KEY="sk-prueba",
                       ASISTENTE_SIEMPRE=True, ASISTENTE_ANONIMIZAR=False)
    @patch("chat.buk.requests.get", side_effect=fake_get)
    @patch("chat.asistente._cliente_openai")
    def test_modo_siempre_manda_todo_al_modelo(self, mock_cliente, mock_buk):
        crear = mock_cliente.return_value.chat.completions.create
        crear.side_effect = [_respuesta(_Mensaje(content="Hoy hay 3 personas fuera."))]
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
                         "¿cuántas personas de comunicaciones están fuera?",
                         "¿por qué hay tanta gente fuera?",
                         "¿cuántos días de vacaciones le quedan a Ana?"):
            self.assertTrue(intents.es_compleja(pregunta), pregunta)

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
@override_settings(ASISTENTE_PROVEEDOR="gemini", GEMINI_API_KEY="AIza-prueba",
                   ASISTENTE_ANONIMIZAR=False)
class GeminiTests(TestCase):
    """El SDK se simula: la suite no consume cuota gratuita."""

    def setUp(self):
        cache.clear()

    def _preguntar(self, texto):
        return self.client.post("/api/chat/", data=json.dumps({"message": texto}),
                                content_type="application/json").json()

    def test_el_proveedor_por_defecto_es_gemini(self):
        from chat import asistente
        self.assertEqual(asistente.proveedor(), "gemini")
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
        self.assertIn("Ana Rojas", cuerpo["answer"])

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

    @override_settings(ASISTENTE_PROVEEDOR="gemini", GEMINI_API_KEY="AIza-prueba")
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
    @override_settings(ASISTENTE_PROVEEDOR="gemini", GEMINI_API_KEY="AIza-prueba",
                       ASISTENTE_TIMEOUT=12)
    def test_el_cliente_de_gemini_lleva_timeout(self):
        """Sin timeout, una llamada colgada deja la pregunta esperando siempre."""
        from chat import asistente
        cliente = asistente._cliente_gemini()
        opciones = cliente._api_client._http_options
        self.assertEqual(opciones.timeout, 12000)  # el SDK los cuenta en ms


@SIN_DOCUMENTOS
@override_settings(ASISTENTE_PROVEEDOR="gemini", GEMINI_API_KEY="AIza-prueba",
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
@override_settings(ASISTENTE_PROVEEDOR="gemini", GEMINI_API_KEY="AIza-prueba",
                   ASISTENTE_ANONIMIZAR=False)
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
        self.assertIn("Ana Rojas", cuerpo["answer"])

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

    @override_settings(ASISTENTE_PROVEEDOR="gemini", GEMINI_API_KEY="AIza-prueba")
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
