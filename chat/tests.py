import json
import tempfile
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from django.conf import settings
from django.core.cache import cache
from django.test import TestCase, override_settings

from chat import buk

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


def _mejor_seccion(mensaje, cuantas=1):
    """La mejor seccion de documentos para una pregunta, o None.

    Equivalente a la vieja `documentos.responder`, que ya no existe: era solo
    para el router de reglas, que llamaba a esto antes de pasarle la pregunta
    a Gemini. La herramienta real que usa el modelo es `buscar_politica`, que
    llama a `documentos.buscar` directamente (ver GeminiTests y
    PoliticaVacacionesDocTests).
    """
    from chat import documentos
    encontradas = documentos.buscar(mensaje, cuantas=cuantas)
    return encontradas[0] if encontradas else None


class _PedidoGemini:
    def __init__(self, nombre, args):
        self.name = nombre
        self.args = args


def _respuesta_gemini(texto=None, llamadas=None, contenido=None):
    return Mock(text=texto, function_calls=llamadas or [],
                candidates=[Mock(content=contenido or Mock())])


@SIN_DOCUMENTOS
class HerramientasTests(TestCase):
    """Las funciones que Gemini puede llamar (`chat/herramientas.py`): mismos
    datos que antes usaba el router de reglas, con la misma sanitizacion."""

    def setUp(self):
        cache.clear()

    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_listar_ausencias_incluye_vacacion_ya_empezada(self, mocked):
        """El caso que fallaba: una vacacion en curso que empezo antes de hoy."""
        from chat import herramientas
        datos = herramientas.listar_ausencias(_f(0), _f(0))
        nombres = [p["nombre"] for p in datos["personas"]]
        # herramientas.listar_ausencias usa el nombre simple, sin el apodo
        # formateado (eso lo redacta Gemini, si lo necesita, via info_persona).
        self.assertIn("Ana Rojas", nombres)     # 24-ago -> 4-sep, en curso
        self.assertIn("Luis Soto", nombres)     # dia administrativo de hoy
        # Luis aparece dos veces (dia administrativo + licencia el mismo dia):
        # un registro por tramo, no por persona. Ver nota en el README sobre
        # "quien esta disponible".
        self.assertEqual(datos["total"], 3)

    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_listar_ausencias_no_expone_datos_sensibles(self, mocked):
        from chat import herramientas
        datos = herramientas.listar_ausencias(_f(-2), _f(2))
        crudo = json.dumps(datos, ensure_ascii=False)
        for sensible in ("11.111.111-1", "22.222.222-2", "luis@azerta.cl", "rut",
                         "accidente_comun", "accidente comun", "licence_type",
                         "post natal"):
            self.assertNotIn(sensible, crudo)

    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_ausencias_de_persona_resuelve_el_nombre(self, mocked):
        from chat import herramientas
        datos = herramientas.ausencias_de_persona("Ana", _f(0), _f(0))
        self.assertTrue(datos["encontrada"])
        self.assertEqual(datos["nombre"], "Ana Rojas")
        self.assertEqual(len(datos["ausencias"]), 1)

    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_ausencias_de_persona_nombre_desconocido(self, mocked):
        from chat import herramientas
        datos = herramientas.ausencias_de_persona("nadie existe de verdad")
        self.assertFalse(datos["encontrada"])

    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_dotacion_cuenta_activos(self, mocked):
        from chat import herramientas
        self.assertEqual(herramientas.dotacion(), {"personas_activas": 2})

    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_cumpleanos_no_expone_el_ano_de_nacimiento(self, mocked):
        from chat import herramientas
        datos = herramientas.cumpleanos(_f(0), 5)
        crudo = json.dumps(datos, ensure_ascii=False)
        self.assertNotIn("1979", crudo)
        self.assertNotIn("birthday", crudo)
        self.assertTrue(any(p["nombre"].startswith("Ana") for p in datos["personas"]))


@SIN_DOCUMENTOS
class NoDisponibleTests(TestCase):
    """Sin clave, sin creditos o con Gemini caido, el chat avisa en vez de
    responder con una version degradada: no hay reglas de respaldo detras."""

    def setUp(self):
        cache.clear()

    def _preguntar(self, texto):
        return self.client.post("/api/chat/", data=json.dumps({"message": texto}),
                                content_type="application/json").json()

    @override_settings(GEMINI_API_KEY="")
    def test_sin_clave_avisa_en_vez_de_inventar(self):
        cuerpo = self._preguntar("quien esta fuera hoy?")
        self.assertEqual(cuerpo["meta"]["intencion"], "no_disponible")
        self.assertEqual(cuerpo["meta"]["motivo"], "sin_clave")
        self.assertIn("No puedo responder ahora mismo", cuerpo["answer"])

    @override_settings(GEMINI_API_KEY="AIza-prueba")
    @patch("chat.asistente._cliente_gemini")
    def test_no_se_registra_como_consulta_pendiente(self, mock_cliente):
        """"No disponible" no es "el modelo no sabe": no hay que revisarla
        despues, hay que arreglar la cuota o la clave."""
        from chat.models import ConsultaNoResuelta
        mock_cliente.return_value.models.generate_content.side_effect = RuntimeError("503")
        self._preguntar("quien esta fuera hoy?")
        self.assertEqual(ConsultaNoResuelta.objects.count(), 0)

    @override_settings(GEMINI_API_KEY="AIza-prueba")
    @patch("chat.asistente._cliente_gemini")
    def test_avisa_el_motivo_especifico_de_la_falla(self, mock_cliente):
        """La primera falla ya alcanza para explicar el motivo real (creditos
        agotados), no solo un generico "no disponible": esto paso en vivo la
        primera vez que se probo el flujo completo."""
        mock_cliente.return_value.models.generate_content.side_effect = RuntimeError(
            "429 RESOURCE_EXHAUSTED. Your prepayment credits are depleted.")
        cuerpo = self._preguntar("quien esta fuera hoy?")
        self.assertEqual(cuerpo["meta"]["motivo"], "prepago_agotado")
        self.assertIn("prepagados", cuerpo["answer"])

    @override_settings(GEMINI_API_KEY="AIza-prueba")
    @patch("chat.asistente._cliente_gemini")
    def test_no_se_cachea(self, mock_cliente):
        mock_cliente.return_value.models.generate_content.side_effect = RuntimeError("503")
        primera = self._preguntar("quien esta fuera hoy?")
        segunda = self._preguntar("quien esta fuera hoy?")
        self.assertFalse(primera["meta"]["desde_cache"])
        self.assertFalse(segunda["meta"]["desde_cache"])


@SIN_DOCUMENTOS
@override_settings(GEMINI_API_KEY="AIza-prueba", ASISTENTE_ANONIMIZAR=False)
class CacheRespuestasTests(TestCase):
    def setUp(self):
        cache.clear()

    def _preguntar_con_modelo(self, texto, mock_cliente):
        generar = mock_cliente.return_value.models.generate_content
        generar.side_effect = [
            _respuesta_gemini(llamadas=[_PedidoGemini(
                "listar_ausencias", {"desde": _f(0), "hasta": _f(0)})]),
            _respuesta_gemini(texto="Hay 3 personas fuera hoy."),
        ]
        return self.client.post("/api/chat/", data=json.dumps({"message": texto}),
                                content_type="application/json").json()

    def _preguntar(self, texto):
        return self.client.post("/api/chat/", data=json.dumps({"message": texto}),
                                content_type="application/json").json()

    @patch("chat.buk.requests.get", side_effect=fake_get)
    @patch("chat.asistente._cliente_gemini")
    def test_la_segunda_vez_no_consulta_buk_ni_al_modelo(self, mock_cliente, mocked):
        self._preguntar_con_modelo("quien esta fuera hoy", mock_cliente)
        llamadas_buk = len(mocked.call_args_list)
        llamadas_modelo = mock_cliente.return_value.models.generate_content.call_count
        cuerpo = self._preguntar("quien esta fuera hoy")
        self.assertTrue(cuerpo["meta"]["desde_cache"])
        self.assertEqual(cuerpo["meta"]["requests_buk"], 0)
        self.assertEqual(len(mocked.call_args_list), llamadas_buk)          # sin red nueva
        self.assertEqual(mock_cliente.return_value.models.generate_content.call_count,
                         llamadas_modelo)                                  # sin tokens nuevos

    @patch("chat.buk.requests.get", side_effect=fake_get)
    @patch("chat.asistente._cliente_gemini")
    def test_ignora_acentos_mayusculas_y_puntuacion(self, mock_cliente, mocked):
        primera = self._preguntar_con_modelo("quien esta fuera hoy", mock_cliente)
        self.assertFalse(primera["meta"]["desde_cache"])
        for variante in ("¿Quién está fuera hoy?", "QUIEN ESTA FUERA HOY!!",
                         "  quien   esta  fuera  hoy  "):
            self.assertTrue(self._preguntar(variante)["meta"]["desde_cache"], variante)

    def test_no_cachea_entre_dias_distintos(self):
        """La respuesta a "hoy" no puede servirse manana."""
        from chat import respuestas
        hoy, manana = date(2026, 9, 3), date(2026, 9, 4)
        self.assertNotEqual(respuestas.clave("quien esta fuera hoy", hoy),
                            respuestas.clave("quien esta fuera hoy", manana))

    @patch("chat.buk.requests.get", side_effect=fake_get)
    @patch("chat.asistente._cliente_gemini")
    def test_no_cachea_las_respuestas_sin_datos(self, mock_cliente, mocked):
        mock_cliente.return_value.models.generate_content.return_value = _respuesta_gemini(
            texto="NO_SE: no tengo ese dato.")
        self._preguntar("cuanto es el aguinaldo?")
        cuerpo = self._preguntar("cuanto es el aguinaldo?")
        self.assertFalse(cuerpo["meta"]["desde_cache"])

    @patch("chat.buk.requests.get", side_effect=fake_get)
    @patch("chat.asistente._cliente_gemini")
    def test_registra_las_preguntas_con_su_frecuencia(self, mock_cliente, mocked):
        from chat.models import Pregunta
        self._preguntar_con_modelo("quien esta fuera hoy", mock_cliente)
        for _ in range(2):
            self._preguntar("quien esta fuera hoy")
        fila = Pregunta.objects.get(mensaje_normalizado__contains="fuera hoy")
        self.assertEqual(fila.veces, 3)
        self.assertEqual(fila.veces_cache, 2)  # la primera no vino de cache


@SIN_DOCUMENTOS
@override_settings(GEMINI_API_KEY="AIza-prueba", ASISTENTE_ANONIMIZAR=False)
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
    def test_si_gemini_falla_avisa_que_no_esta_disponible(self, mock_cliente, mock_buk):
        mock_cliente.return_value.models.generate_content.side_effect = RuntimeError("429")
        cuerpo = self._preguntar("hazme un resumen de la carga del equipo")
        self.assertEqual(cuerpo["meta"]["intencion"], "no_disponible")


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
        doc = ("Planiﬁcacion:\nLa planiﬁcacion de vacaciones la coordina cada "
               "director de cuenta antes de ingresar la solicitud en la plataforma.\n")
        with self._con(doc):
            seccion = _mejor_seccion("como es la planificacion de vacaciones")
        self.assertIsNotNone(seccion)

    def test_la_seccion_larga_no_gana_por_volumen(self):
        doc = ("Generalidades:\n" + "vacaciones dias feriado solicitud equipo " * 40 + "\n"
               "Enfermedad:\nSi el colaborador se enferma durante sus vacaciones puede "
               "solicitar la reprogramacion presentando la licencia.\n")
        with self._con(doc):
            seccion = _mejor_seccion("que pasa si me enfermo en vacaciones")
        self.assertEqual(seccion["titulo"], "Enfermedad")

    def test_la_herramienta_entrega_varias_secciones(self):
        from chat import herramientas
        with self._con(self.DOC):
            salida = herramientas.buscar_politica("dias administrativos y feriado legal")
        self.assertTrue(salida["encontrada"])
        self.assertGreaterEqual(len(salida["secciones"]), 1)
        self.assertIn("titulo", salida["secciones"][0])


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
        carpeta, _ = self._pdf([
            "Dias administrativos:\nCada colaborador tiene un dia administrativo "
            "por semestre y no se acumula al siguiente."
        ])
        with override_settings(DOCUMENTOS_DIR=carpeta):
            seccion = _mejor_seccion("dia administrativo por semestre")
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
        carpeta = tempfile.mkdtemp()
        (Path(carpeta) / "politica.txt").write_text(
            "Dias administrativos:\nCada colaborador tiene derecho a un dia "
            "administrativo por semestre, que no se acumula.\n", encoding="utf-8")
        with override_settings(DOCUMENTOS_DIR=carpeta):
            seccion = _mejor_seccion("dias administrativos por semestre")
        self.assertIsNotNone(seccion)


@SIN_DOCUMENTOS
class ReevaluarTests(TestCase):
    def setUp(self):
        cache.clear()

    @override_settings(GEMINI_API_KEY="AIza-prueba")
    @patch("chat.buk.requests.get", side_effect=fake_get)
    @patch("chat.asistente._cliente_gemini")
    def test_cierra_las_consultas_que_los_documentos_ya_responden(self, mock_cliente, mocked):
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

        # El orden en que "reevaluar" procesa las pendientes no esta
        # garantizado (depende de la marca de tiempo de cada una), asi que la
        # respuesta se decide mirando el contenido de la pregunta, no la
        # posicion en una lista fija.
        vueltas = {"administrativo": 0}

        def responder_segun_pregunta(*args, **kwargs):
            contents = kwargs.get("contents", [])
            texto = " ".join(str(c) for c in contents).lower()
            if "administrativo" in texto:
                vueltas["administrativo"] += 1
                if vueltas["administrativo"] == 1:
                    return _respuesta_gemini(llamadas=[_PedidoGemini(
                        "buscar_politica", {"consulta": "dias administrativos por semestre"})])
                return _respuesta_gemini(texto="Un dia administrativo por semestre.")
            return _respuesta_gemini(texto="NO_SE: no tengo ese dato.")

        mock_cliente.return_value.models.generate_content.side_effect = responder_segun_pregunta

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
        carpeta = tempfile.mkdtemp()
        (Path(carpeta) / "politica.txt").write_text(
            "Dias administrativos:\nCada colaborador tiene derecho a un dia "
            "administrativo por semestre, que no se acumula al siguiente.\n",
            encoding="utf-8")
        with override_settings(DOCUMENTOS_DIR=carpeta):
            self.assertIsNone(_mejor_seccion("¿quién ganó el partido de ayer?"))
            self.assertIsNone(_mejor_seccion("cuál es el anexo de recepción"))


@SIN_DOCUMENTOS
class CumpleanosTests(TestCase):
    """Día y mes sí; el año de nacimiento nunca sale de la capa de datos."""

    def setUp(self):
        cache.clear()

    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_no_expone_el_ano_de_nacimiento(self, mocked):
        from chat import buk
        personas, _ = buk.directorio()
        crudo = json.dumps(list(personas.values()), ensure_ascii=False)
        self.assertNotIn("1979", crudo)          # el año del fixture
        self.assertNotIn("birthday", crudo)
        self.assertEqual(len(list(personas.values())[0]["cumple"]), 5)  # solo MM-DD

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
    """El chat necesita acordarse de lo ya hablado para entender un
    seguimiento como "y esta disponible hoy?"."""

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


@override_settings(GEMINI_API_KEY="AIza-prueba")
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


class PersonaPorCargoTests(TestCase):
    """"Quien es el gerente de X" no nombra a nadie: hay que buscar por el
    texto del cargo. `persona_por_cargo` (chat/herramientas.py) hace esa
    busqueda; decidir cuándo usarla en vez de `info_persona` es cosa de
    Gemini, no del código."""

    def setUp(self):
        cache.clear()

    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_herramienta_persona_por_cargo(self, mocked):
        from chat import herramientas
        resultado = herramientas.persona_por_cargo("analista")
        self.assertTrue(resultado["encontrada"])
        self.assertEqual(resultado["nombre"], "Ana Rojas")

    @patch("chat.buk.requests.get", side_effect=fake_get)
    def test_cargo_inexistente_no_inventa(self, mocked):
        from chat import herramientas
        resultado = herramientas.persona_por_cargo("gerente de finanzas")
        self.assertFalse(resultado["encontrada"])


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

    @override_settings(GEMINI_API_KEY="AIza-prueba", ASISTENTE_FALLAS_MAX=3)
    def test_una_falla_aislada_conserva_el_motivo_aunque_siga_disponible(self):
        """La ULTIMA pregunta pudo fallar por creditos agotados sin que el
        cortacircuitos se active todavia (necesita varias fallas seguidas):
        el chat tiene que poder explicar ese error igual, no solo cuando ya
        esta en pausa."""
        from chat import asistente
        asistente.registrar_falla(RuntimeError(
            "429 RESOURCE_EXHAUSTED. Your prepayment credits are depleted."))
        estado = asistente.estado()
        self.assertTrue(estado["disponible"])           # 1 falla no activa la pausa
        self.assertEqual(estado["motivo"], "prepago_agotado")
        self.assertIn("prepagados", estado["motivo_legible"])

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
