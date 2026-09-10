# Iris

Asistente interna de Azerta. Centraliza la información operacional de la empresa
—nómina, disponibilidad del equipo, políticas y procedimientos— en una
conversación. La clave de BUK nunca se expone al navegador.

## Ejecutar

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edita .env y agrega BUK_API_KEY y la ruta autorizada de BUK
python manage.py check
python manage.py test
python manage.py runserver
```

Abre `http://127.0.0.1:8000/`. Vas a caer en la pantalla de login — toda la
app pide sesión con Google, ver la sección [Login](#login) para configurar
las credenciales la primera vez.

Para probar los tres formatos de autenticación sin mostrar la clave:

```bash
python scripts/check_buk_auth.py
```

### Para varios usuarios a la vez

`runserver` es solo para desarrollo: atiende una consulta a la vez de forma
confiable, pero no es lo que hay que dejar corriendo con gente real
preguntando en paralelo. Para eso:

```bash
gunicorn config.wsgi:application --workers 3 --threads 2 --bind 0.0.0.0:8000
```

`--threads` importa más que `--workers` aca: esperar la respuesta de Gemini o
de BUK es tiempo muerto de red, no de CPU, así que varios hilos por worker
aprovechan esa espera sin gastar más memoria en procesos. 3 workers × 2 hilos
alcanza de sobra para el tamaño de Azerta; si la cola se nota, subir primero
`--workers`.

Lo que ya está preparado para esto en el código:

- **SQLite en modo WAL** (`config/settings.py::DATABASES`): sin esto, cada
  `registrar()`/`contar()` bloquea el archivo completo mientras escribe, y
  una pregunta que solo lee tendría que esperar. Con varias personas
  preguntando a la vez es la diferencia entre notar el bloqueo o no.
- **El cliente de Gemini se reutiliza entre preguntas**, no se crea uno
  nuevo por request (`chat/asistente.py::_cliente_gemini`), con un lock para
  que dos hilos no lo reconstruyan a la vez.
- **El caché y el cortacircuitos ya eran compartidos entre procesos**
  (`CACHE_BACKEND` en base de datos, no en memoria): con varios workers de
  gunicorn, todos ven la misma pausa si Gemini falla, no una por proceso.

## Configuración BUK

- `BUK_API_KEY`: clave entregada por BUK.
- `BUK_API_BASE`: raíz de la API, por defecto `https://azerta.buk.cl/api/v1/chile`.
- `BUK_AUTH_HEADER`: header esperado por la API, por defecto `auth_token`.
- `BUK_AUTH_PREFIX`: prefijo opcional del token, por ejemplo `Bearer `.
- `BUK_CACHE_TTL`: segundos que se cachea el directorio de personas (600).
- `BUK_ABSENCE_CACHE_TTL`: segundos que se cachea una consulta de ausencias (60).
- `BUK_VACACIONES_MARGEN_DIAS`: días hacia atrás al pedir `/vacations` (120).

La cuenta necesita permisos de lectura sobre **Personas, Empleados, Vacaciones y
Asistencia**. Sin el de Vacaciones, `/vacations` responde `401` y el asistente
reporta que nadie está de vacaciones.

## Qué entiende el chat

Toda pregunta la responde Gemini: decide qué herramienta de
`chat/herramientas.py` llamar (o si no necesita ninguna) y redacta la
respuesta con lo que esas herramientas le devuelven. No hay un router de
reglas por delante — ver [Modelo de lenguaje: Gemini](#modelo-de-lenguaje-gemini).

Las herramientas resuelven contra `chat/buk.py`, que le pide a BUK la fuente
que corresponde:

| Pregunta | Herramienta → fuente |
| --- | --- |
| ¿Quién está fuera hoy? | `listar_ausencias` → `/vacations` + `/absences`, combinadas |
| ¿Quién está de vacaciones hoy? | `listar_ausencias` → `/vacations` |
| Días administrativos en septiembre | `listar_ausencias`, subtipo `dias_administrativos` |
| Licencias médicas esta semana | `listar_ausencias` → `/absences?type=licence` |
| ¿Quién faltó ayer? | `listar_ausencias` → `/absences?type=absence` |
| ¿Cuántas personas hay activas? | `dotacion` → solo el directorio cacheado |

**Las vacaciones no están en `/absences`.** Viven en `/vacations` (tipos
`legales`, `dias_administrativos`, `progresivas`, `dias_adicionales`). El
`paid_leave` de `/absences` es *permiso con goce*, otra cosa. Confundirlos hace
que el asistente reporte que nadie está de vacaciones cuando sí lo está.

## Cómo se optimizan las solicitudes

1. **BUK filtra, no Python.** `/absences` acepta `type`, `from`, `to` y
   `page_size`; `from`/`to` seleccionan por solapamiento con el rango.
2. **`/vacations` es distinto: no filtra por rango.** Su parámetro `date`
   devuelve las vacaciones que *empiezan* desde esa fecha, no las que la cubren
   — usarlo directo pierde a quien ya estaba de vacaciones. Se pide con
   `BUK_VACACIONES_MARGEN` días hacia atrás (120 por defecto, contra un máximo
   histórico de 58 días de duración) y el solapamiento se resuelve localmente.
   Eso baja de 15 páginas a 2 sin perder registros.
3. **Directorio cacheado.** Los 99 empleados caben en una página de 200 y se
   cachean 10 minutos, así que solo el primer mensaje paga esa lectura.
4. **Caché corta por consulta.** Preguntas repetidas dentro de 60 s no vuelven a
   salir a la red.
5. **Nada de datos sensibles.** `/absences` no trae PII. Del directorio solo
   sobreviven `id`, `nombre` y `cargo` (`current_job.role.name`); el RUT, la
   dirección, la cuenta bancaria, la previsión y el RUT de la jefatura
   (`current_job.boss.rut`) nunca cruzan `chat/buk.py`.

Resultado: **1 request** para preguntas de licencias o permisos, **2** cuando
hay vacaciones involucradas, **0** si la respuesta está cacheada.

## Preguntas por una persona

Si la pregunta nombra a alguien de la nómina, Gemini usa `ausencias_de_persona`
o `info_persona` (según si se pregunta por disponibilidad o por identidad/
cargo/cuentas) en vez de listar a todo el mundo — la instrucción está en
`INSTRUCCIONES` dentro de `chat/asistente.py`.

El nombre se resuelve contra el directorio con `chat/personas.py::buscar`.
Basta el nombre o el apellido (`¿cuándo vuelve Duk?`). Si no encuentra a nadie
o coincide con varias personas, la herramienta se lo dice a Gemini
(`"encontrada": false`, con los candidatos si los hay) y el modelo se lo
explica al usuario en vez de inventar o adivinar.

## Documentos internos

Los documentos de política salen de una de dos fuentes, según
`DOCUMENTOS_FUENTE`:

- **`local`** (por defecto): cualquier `.md`, `.txt`, `.pdf` o `.docx` en
  `datos/`. Los archivos que empiezan con `LEEME` o `README` se ignoran.
- **`drive`**: una carpeta compartida de Google Drive (ver
  [Documentos desde Google Drive](#documentos-desde-google-drive)).

En los dos casos el resto es igual: **el bot no "aprende" el documento**, no
hay entrenamiento. El archivo se lee cuando llega la pregunta y se le entrega
la sección pertinente. Editarlo cambia las respuestas en el acto, y borrarlo
las quita.

> **La planilla de cuentas (`Personas Hrs Sem x Cuenta.xlsx`, con RUTs y horas
> contractuales) se lee SIEMPRE de `datos/` local**, nunca de Drive, y sigue
> sin entrar al corpus de búsqueda (`.xlsx` no está en `EXTENSIONES`).

### Documentos desde Google Drive

Con `DOCUMENTOS_FUENTE=drive`, `chat/drive.py` sincroniza una carpeta
compartida a `DRIVE_CACHE_DIR` (`.drive_cache/`, gitignoreado) y el pipeline
lee de ahí como si fuera `datos/`. Baja solo lo que cambió (firma
`fileId + modifiedTime`) y borra del caché lo que se borró en Drive.

| En Drive | Qué pasa |
| --- | --- |
| Google Doc nativo | se exporta a Markdown |
| PDF / `.txt` / `.md` / `.docx` subido | se baja tal cual (`.docx` se lee con `python-docx`, los encabezados de estilo pasan a títulos Markdown) |
| Google Sheets / Slides, `.xlsx`, otros | se omiten |

**Acceso: una cuenta de servicio de solo lectura.** No se pide permiso de
escritura ni acceso a todo Drive — la cuenta solo ve la carpeta que le
compartas.

1. En [Google Cloud Console](https://console.cloud.google.com/), en el mismo
   proyecto del login: **APIs y servicios → Habilitar API → Google Drive API**.
2. **IAM y administración → Cuentas de servicio → Crear**. Nombre: `iris-drive`.
   No hace falta darle ningún rol de proyecto.
3. En la cuenta creada: **Claves → Agregar clave → JSON**. Se descarga un
   archivo; guardalo fuera del repo y apuntá `GOOGLE_DRIVE_CREDENTIALS` a esa
   ruta (el `.gitignore` ya excluye `*.service-account.json` y `.drive_cache/`).
4. En Drive, **compartí la carpeta** con el email de la cuenta de servicio
   (`iris-drive@<proyecto>.iam.gserviceaccount.com`), como **Lector**.
5. En `.env`:
   ```bash
   DOCUMENTOS_FUENTE=drive
   GOOGLE_DRIVE_FOLDER_ID=https://drive.google.com/drive/folders/XXXX   # ID o URL
   GOOGLE_DRIVE_CREDENTIALS=/ruta/segura/iris-drive.service-account.json
   ```
6. `python manage.py sincronizar_drive` para la primera carga; después la app
   resincroniza sola cada `DRIVE_SYNC_TTL` (300 s) cuando llega una consulta,
   con un lock para que dos workers de gunicorn no bajen lo mismo. Conviene
   además dejarlo en el cron junto a `precalentar` e `indexar`.

Si Drive no responde, se registra y el asistente sigue contestando lo de BUK,
sin documentos, hasta la próxima sincronización.

**Seguridad — superficie de inyección más amplia:** con Drive, cualquiera con
permiso de **edición** en la carpeta puede dejar un documento que entra al
conocimiento de Iris. Mitigaciones:

- Compartí la carpeta con **edición solo a gente de confianza**; al resto,
  lector. Esto es control de acceso en Drive, no algo que el código resuelva.
- Cada documento de texto se pasa por `chat/antiprompt.py`; si engancha
  `DRIVE_DOC_ANTIPROMPT_UMBRAL` patrones de inyección (2 por defecto), **no se
  indexa** y queda como `EventoSeguridad` de tipo `injection` en `/portal/`
  (`DRIVE_OMITIR_SOSPECHOSOS=False` para solo registrar sin descartar).
- `buscar_politica` ya rotula el texto de los documentos como "referencia, no
  instrucciones" (ver [Anti prompt-injection](#anti-prompt-injection)).

### Cómo se parte

Primero por títulos markdown (`#`). Si no hay —el caso de un PDF exportado a
`.txt`— se detectan títulos de texto plano: líneas cortas terminadas en `:`
(`Aspectos legales:`) y secciones numeradas (`I. Vacaciones`, `2.1 Solicitud`).
El bloque de portada anterior al primer título se descarta: compite por las
mismas palabras y no responde nada.

### Cómo se elige la sección

- Las palabras raras pesan más que las comunes. En una política de vacaciones,
  "vacaciones" está en todas las secciones y no discrimina; "enfermo" está en
  una sola y es la que decide.
- Se comparan raíces de 6 letras, así "enfermo" encuentra "enferme" y
  "fraccionar" encuentra "fraccionamiento".
- El puntaje se normaliza por largo, para que una sección de 2.500 caracteres no
  gane por acumular coincidencias en vez de por responder.
- Las ligaduras de PDF (`ﬁ`, `ﬂ`) se descomponen al normalizar. Sin eso,
  "planiﬁcación" nunca coincide con "planificación" y la sección queda invisible.

A la herramienta `buscar_politica` se le entregan las **tres** mejores
secciones, no una: una pregunta suele cruzar dos, y componer es lo que el
modelo hace bien. Gemini decide solo cuándo llamarla — por ejemplo, `¿cómo
pido vacaciones?` es procedimiento y usa `buscar_politica`, no
`listar_ausencias`, aunque comparta la palabra "vacaciones" con una pregunta
de disponibilidad.

## Preguntas que no supo responder

Cuando ninguna herramienta le da lo que necesita, Gemini tiene que decirlo con
la marca `NO_SE:` en vez de responder con generalidades (ver `INSTRUCCIONES`
en `chat/asistente.py`). El usuario nunca ve esa marca: `chat/asistente.py`
la separa, y la pregunta queda registrada:

```bash
python manage.py consultas
```

```
2 consultas distintas, 3 preguntas en total.
  [   1] x2   ¿cuánto es el bono de fin de año?      No se entendio la pregunta
  [   2] x1   ¿está Javiera fuera hoy?               El nombre coincide con varias personas
```

Las repeticiones se agrupan en una fila, así que el orden por frecuencia indica
qué conviene cubrir primero. Para marcar una como resuelta:
`python manage.py consultas --resolver 1`.

### Backlog manual: propuestas y mejoras a futuro

Ese backlog es automático: solo se llena con preguntas reales que alguien le
hizo al chat. Para ideas —una pregunta que todavía nadie le hizo a Iris pero
conviene cubrir, una mejora, cualquier idea a futuro— el botón **Propuesta**,
junto al indicador de conexión en la parte superior de la página, lleva a
`/propuestas/`: un formulario abierto a **cualquiera**, sin iniciar sesión ni
decir quién es (`chat/views.py::propuestas_nueva`, `chat/forms.py::PropuestaForm`).

Quien propone solo elige categoría (`pregunta` / `mejora` / `otro`), un
título y, opcionalmente, una descripción — no puede fijar el estado. Revisar
la lista y cambiarle el estado (`pendiente` / `en_progreso` / `hecha` /
`descartada`) sí requiere sesión: se hace desde `/admin/chat/propuesta/`,
igual que con `ConsultaNoResuelta`.

## Confidencialidad

Además de los datos que nunca salen del backend (RUT, dirección, cuenta
bancaria, previsión), el **motivo de una licencia médica** (`licence_type`: pre
natal, accidente común, etc.) se descarta en `chat/buk.py` al normalizar el
registro. Es información de salud y no entra a la aplicación.

## Roles y portal de administración

Toda persona logueada tiene un **rol** que acota qué puede preguntar
(`chat/models.py::PerfilUsuario`). La barrera real está en
`chat/autorizacion.py`, que se ejecuta **entre Gemini y las herramientas**:
cuando el modelo pide una herramienta, la política del rol decide si la deja
correr, si le filtra el resultado o si la niega. No se confía en el prompt
para esto — un modelo confundido o manipulado no puede saltarse el filtro
porque el filtro está en el código, sobre el resultado.

| Rol | Qué puede preguntar |
| --- | --- |
| `gerencia` | Todo, sobre cualquier persona. (Un superusuario de Django es `gerencia` automáticamente.) |
| `ejecutivo` | Información general (políticas, cuentas, dotación, cumpleaños del mes) y datos de personas **de su misma familia de rol en BUK** (`role_family`: Ejecutivos, Consultores, Directores…) o de sí mismo. Beneficios: solo los propios. |
| `sin_acceso` | Nada. El chat responde que su cuenta no tiene acceso. |

- **Quién es quién:** `chat/perfil.py` cruza la cuenta de Google
  (`user.email`) contra el empleado de BUK que tiene ese correo. Sin match
  (contratista, cuenta de servicio), un ejecutivo solo se puede consultar a sí
  mismo. Se puede forzar el cruce con `PerfilUsuario.buk_employee_id`.
- **Alta nueva:** un usuario sin rol asignado entra como `ejecutivo`
  (acotado), no bloqueado. El staff lo sube desde el portal si corresponde.
- **Listados vs. consulta puntual:** un listado (`¿quién está fuera hoy?`)
  simplemente omite a quien está fuera de alcance; preguntar por una persona
  puntual fuera de alcance responde "no tienes acceso a ese dato".
- **Caché por alcance:** `chat/respuestas.py` llavea el caché por rol/familia,
  para que un ejecutivo no reciba la respuesta completa que se armó para un
  gerente.

### El portal: `/portal/`

Página dentro de la app, **solo para staff** (`is_staff`), detrás del login de
Google. Separada del `/admin/` de Django:

- **Usuarios y roles** (`/portal/`): todas las cuentas, su match en BUK
  (nombre, cargo, área, familia de rol) y su rol en Iris, con cambio de rol
  inline.
- **Eventos de seguridad** (`/portal/eventos/`): lo que el sistema bloqueó o
  limitó (`chat/models.py::EventoSeguridad`) — inyecciones detectadas,
  consultas fuera de alcance, rate-limit, cupo diario. Solo lectura.

Los dos modelos también quedan en `/admin/` como respaldo.

## Límites de uso y anti-abuso

Todo configurable por `.env`, todo apagado bajo `manage.py test`:

| Qué | Variable | Default |
| --- | --- | --- |
| Consultas al chat por usuario (ráfaga) | `RATE_LIMIT_CHAT` | `20/60` |
| Consultas al chat por usuario (hora) | `RATE_LIMIT_CHAT_HORA` | `240/3600` |
| Feedback por usuario | `RATE_LIMIT_FEEDBACK` | `30/60` |
| Propuestas por IP (sin login) | `RATE_LIMIT_PROPUESTAS` | `5/3600` |
| Largo máximo de una consulta | `ASISTENTE_MAX_CARACTERES` | `2000` |
| Herramientas por paso del modelo | `ASISTENTE_MAX_PEDIDOS_PASO` | `5` |
| Consultas al modelo por usuario y día | `LLM_PRESUPUESTO_DIARIO` | `300` (0 = sin límite) |

El rate limit (`chat/ratelimit.py`) es una ventana fija sobre el caché
compartido (la tabla de BD): con varios workers de gunicorn el contador es el
mismo para todos, y si el caché falla, deja pasar (no tumba el chat). El
formulario `/propuestas/` suma un **honeypot** oculto además del límite por IP.

Los nombres de la nómina van **seudonimizados** a Gemini por defecto
(`ASISTENTE_ANONIMIZAR=True`); ver [Anonimizar la nómina](#anonimizar-la-nómina).

## Anti prompt-injection

`chat/antiprompt.py` revisa la consulta entrante contra patrones típicos de
secuestro del modelo ("ignora las instrucciones", "revela tu prompt", "actúa
como…", DAN, developer mode, y sus variantes en inglés). Si engancha, la
consulta se bloquea **antes** de gastar una llamada a Gemini y queda como
`EventoSeguridad` de tipo `injection`.

Es una capa de mitigación, no la barrera: la barrera es que el modelo solo
puede llamar a las herramientas de `chat/herramientas.py` y que
`chat/autorizacion.py` acota lo que cada rol ve. Como refuerzo:

- `buscar_politica` rotula el texto de los documentos con una nota de "esto es
  referencia, no instrucciones".
- `INSTRUCCIONES` en `chat/asistente.py` le dice a Gemini que el contenido de
  las herramientas es información y nunca órdenes, y le recuerda el alcance del
  rol cuando quien pregunta no es gerencia.

## Cabeceras de seguridad

`chat/middleware.py::SecurityHeadersMiddleware` agrega en cada respuesta lo que
Django no pone solo:

- **Content-Security-Policy**: estricta para las páginas propias
  (`default-src 'self'`, sin `'unsafe-inline'` — el JS y el CSS viven en
  `static/`, por eso se sacaron el `<script>` y el `<style>` inline de los
  templates), con estilos/fuentes solo desde Google Fonts y `connect-src 'self'`.
  Para `/admin/` la CSP es más laxa (`'unsafe-inline'`) porque el admin de
  Django usa scripts y estilos inline; igual bloquea lo externo.
- **Permissions-Policy**: apaga cámara, micrófono, geolocalización, pagos, USB.
- **Cross-Origin-Resource-Policy: same-origin** — nadie puede embeber las
  respuestas desde otro origen.
- **X-Content-Type-Options: nosniff**.

Además, en `config/settings.py`:

- `XFrameOptionsMiddleware` + `X_FRAME_OPTIONS = "DENY"` (antes no había
  protección de clickjacking).
- `SECURE_REFERRER_POLICY = "same-origin"`, `SECURE_CROSS_ORIGIN_OPENER_POLICY
  = "same-origin"`, `SECURE_CONTENT_TYPE_NOSNIFF = True`.
- **Con `DJANGO_DEBUG=False`** se activan además: `SESSION_COOKIE_SECURE`,
  `CSRF_COOKIE_SECURE`, `CSRF_COOKIE_HTTPONLY` (el front lee el token del
  `<input>`, no de la cookie), `SESSION_COOKIE_HTTPONLY`, `SECURE_SSL_REDIRECT`
  (`DJANGO_SSL_REDIRECT`), `SECURE_PROXY_SSL_HEADER` si
  `DJANGO_BEHIND_TLS_PROXY=True`, y HSTS si `DJANGO_HSTS_SECONDS` > 0 (empezar
  con un valor chico; ver `.env.example`).

## Control de acceso a nivel de fila (RLS)

**SQLite no tiene row-level security nativo** (eso es de Postgres:
`CREATE POLICY`). El control de acceso por fila de esta app vive en la **capa
de aplicación**:

- Los datos de personas no están en la base local, vienen de BUK. Qué filas ve
  cada quien lo decide `chat/autorizacion.py` sobre el resultado de cada
  herramienta (ver [Roles y portal](#roles-y-portal-de-administración)).
- Las tablas propias (`ConsultaNoResuelta`, `Pregunta`, `Propuesta`,
  `PerfilUsuario`, `EventoSeguridad`) solo se leen desde `/portal/` y `/admin/`,
  ambos detrás de `is_staff`.
- **Escalar un `PerfilUsuario` a `gerencia`** (acceso total) requiere
  `is_superuser`, en `/portal/` y en el admin. Un staff común administra
  `ejecutivo` / `sin_acceso`, no reparte god-mode.
- **`POST /admin/login/`** tiene rate limit por IP
  (`RATE_LIMIT_ADMIN_LOGIN`, `chat/middleware.py::AdminBruteForceMiddleware`):
  es la única puerta que no pasa por Google y la que ve todas las filas.

Si en el futuro se migra a Postgres, se puede sumar RLS de base de datos como
defensa en profundidad, pero es un proyecto aparte.

## CORS

La API (`/api/chat/`, `/api/status/`, `/api/feedback/`) es **solo del mismo
origen**: el front se sirve del mismo dominio y solo hace `fetch` a rutas
propias. **No hay ningún header `Access-Control-Allow-*`** — que es lo más
seguro: sin CORS, el navegador no deja que ningún origen ajeno lea las
respuestas. Hay un test que lo verifica (`SecurityHeadersTests`).

- **`CSRF_TRUSTED_ORIGINS`**: en producción se define explícito y con scheme
  por `DJANGO_CSRF_ORIGINS` (`https://iris.azerta.cl`). El fallback
  `http://<host>:8000` —para entrar por IP de la red local en desarrollo— solo
  se agrega con `DJANGO_DEBUG=True`.
- Si algún día hace falta acceso cross-origin (una SPA aparte, una app móvil),
  se agrega `django-cors-headers` con un allowlist **explícito y corto**, nunca
  `CORS_ALLOW_ALL_ORIGINS`.

## Modelo de lenguaje: Gemini

Gemini responde el 100% de las preguntas. No hay un router de reglas detrás:
si no hay clave, si se acabaron los créditos o la cuota, o si el proveedor no
responde, el asistente lo dice explícitamente (`chat/views.py::responder_no_disponible`)
en vez de contestar con una versión degradada.

Es el único proveedor — hubo un respaldo con OpenAI mientras se evaluaba el
gasto, se sacó del código al aprobarse el presupuesto de Gemini.

```bash
# en .env
GEMINI_API_KEY=...
GEMINI_MODEL=gemini-3.6-flash
```

Los nombres de modelo cambian seguido, y no todos siguen disponibles para
cuentas nuevas. Para ver cuáles habilita tu clave:

```bash
python manage.py modelos
```

Avisa si el valor de `GEMINI_MODEL` no está en la lista. Verificado con
`gemini-3.6-flash`; `gemini-2.5-flash` responde 404 para cuentas nuevas y
`gemini-3.8-flash` devolvía 503 por saturación.

### Detalle de implementación: thought_signature

Los modelos Gemini 3.x firman cada `functionCall` con un `thought_signature` y
**exigen recibirlo de vuelta** en el turno siguiente. Por eso el bucle reenvía
`respuesta.candidates[0].content` tal cual, en vez de rearmar las partes con
`Part.from_function_call()`: reconstruirlas pierde la firma y la API responde
`400 INVALID_ARGUMENT`.

**Si Gemini pide varias herramientas en el mismo paso, corren en paralelo**
(`chat/asistente.py::_ejecutar_pedidos`), no una tras otra: "compara agosto
contra septiembre" pide `listar_ausencias` dos veces, y son dos consultas de
red independientes. Lo único que se hace en serie después es anotar qué se
llamó y anonimizar (si `ASISTENTE_ANONIMIZAR` está activo): esas dos partes
comparten estado entre pedidos y hacerlas desde varios hilos arriesgaría un
seudónimo duplicado para la misma persona.

**Al modelo no se le entrega la API de BUK.** Solo puede llamar a las funciones
declaradas en `chat/herramientas.py`, que sanitizan lo que sale de BUK, los
documentos y la planilla de cuentas:

| Herramienta | Qué hace |
| --- | --- |
| `listar_ausencias` | quién no está en su jornada en un rango |
| `ausencias_de_persona` | situación de una persona (resuelve el nombre localmente) |
| `info_persona` | quién es alguien: cargo, área y cuentas que atiende |
| `equipo_de` | quién compone una cuenta/cliente o un área |
| `persona_por_cargo` | quién ocupa un cargo, cuando la pregunta no nombra a nadie |
| `cumpleanos` | quién cumple años en un rango (solo día y mes) |
| `cumpleanos_de_persona` | cuándo cumple años una persona puntual |
| `dotacion` | cantidad de personas activas |
| `quien_esta_trabajando` | quién SI está en su jornada, con nombres (opcionalmente por cuenta/área) |
| `listar_cuentas` | nombres de todas las cuentas/clientes que administra Azerta |
| `listar_beneficios` | catálogo de beneficios de Azerta (módulo Beneficios de BUK) |
| `beneficios_de_persona` | qué beneficios solicitó una persona y en qué estado está cada uno |
| `buscar_politica` | busca en los documentos de `datos/` |

La clave de BUK nunca sale del backend, el modelo no ve el payload crudo, y el
motivo de una licencia médica ya viene descartado desde `chat/buk.py`.

### Qué pasa si Gemini no está disponible

No hay reglas de respaldo detrás: si Gemini no responde, el chat lo dice.

- **Cortacircuitos.** Tras `ASISTENTE_FALLAS_MAX` fallas seguidas (3), se deja
  de llamar al proveedor por `ASISTENTE_PAUSA_SEGUNDOS` (180): las siguientes
  preguntas reciben el aviso al instante, sin esperar el timeout de cada
  intento. Una respuesta exitosa lo reactiva.
- **El motivo se clasifica y se muestra.** `chat/asistente.py::_clasificar_error`
  distingue sin clave, cuota gratuita agotada, créditos prepagados agotados, y
  clave inválida — el indicador junto al logo en la interfaz (y
  `GET /api/status/`) muestra cuál es.
- **Nada de esto se cachea.** Una respuesta de "no disponible" no se guarda en
  el caché de `chat/respuestas.py`: si se cacheara, seguiría mostrándose
  después de que Gemini se recupere.

## Cumpleaños

`¿quién está de cumpleaños?`, `¿quién cumple años este mes?`, `cumpleaños en
octubre`. Los 98 empleados activos tienen la fecha en BUK.

**Del cumpleaños solo se guarda `MM-DD`.** El año revela la edad, no hace falta
para saludar a nadie y es un dato sensible: se descarta en `chat/buk.py` y no
cruza esa capa.

Dos herramientas, la misma distinción que `listar_ausencias`/`ausencias_de_persona`:

- **`cumpleanos(rango)`** para preguntas sin nombre — `rango` acepta `hoy` /
  `esta_semana` / `este_mes` y calcula el límite de calendario exacto en
  código (`chat/herramientas.py::_rango_calendario`): una semana es lunes a
  domingo, un mes es del 1 al último día de ESE mes (bisiesto incluido), no
  "7" o "30 días desde hoy". También acepta `desde`/`dias` para un rango a
  medida ("en octubre").
- **`cumpleanos_de_persona(nombre)`** para "cuándo cumple años X". No
  reutiliza `cumpleanos` con un rango amplio: esa reutilización recortaba a
  `MAX_PERSONAS` (60) el resultado, y con casi 100 empleados activos quien
  cumplía años más lejos en el calendario podía quedar fuera del recorte —
  el modelo terminaba diciendo que no sabía sobre un dato que BUK sí tiene.
  Esta busca directo a esa persona, sin ese límite.

Los días que faltan se cuentan **desde hoy**, no desde el inicio del rango: al
preguntar por "este mes" el día 7, uno del día 6 se marca "ya pasó" en vez de
"en 5 días" (`chat/buk.py::cumpleanos`).

## Quiénes componen un equipo

`¿quiénes están en el equipo de CENCOSUD?` es una pregunta distinta de `¿quién
está disponible en CENCOSUD?`: la primera es por composición (`equipo_de`), la
segunda por disponibilidad de hoy (`listar_ausencias`, filtrando por lo que
`equipo_de` devuelve). `INSTRUCCIONES` en `chat/asistente.py` le deja claro a
Gemini cuál es cuál.

Gemini tiene el historial de la conversación (`chat/asistente.py`, memoria en
la sesión) y lo usa para entender un seguimiento corto ("y están disponibles?"
después de preguntar por un equipo) sin que el usuario repita el nombre — pero
la decisión de a qué se refiere el seguimiento la toma el modelo, no una regla
de código.

## Quién está disponible

`¿quién está trabajando hoy?`, `¿está todo el equipo?`. Antes no había una
herramienta dedicada: Gemini tenía que calcularlo combinando `dotacion` (solo
da un número) con `listar_ausencias`, sin poder enumerar nombres de quien SI
está. `quien_esta_trabajando` (`chat/herramientas.py`) resuelve eso —
directorio completo menos quien aparece en `listar_ausencias` en el mismo
rango, con el mismo filtro opcional de cuenta/área que `equipo_de`.

**Ojo:** `listar_ausencias` devuelve un registro por cada tramo, no uno por
persona — quien parte sus vacaciones en tres tramos aparece tres veces en la
lista. El router de reglas que había antes los agrupaba antes de contar; ahora
esa suma la tiene que hacer Gemini razonando sobre la lista cruda, así que
"cuántas personas" es más frágil que antes para casos con tramos partidos.

## Apodos

La gente pregunta por el apodo mucho más que por el nombre completo. El apodo
sale de BUK (`custom_attributes.Apodo`, 83 de 98 lo tienen), no de la planilla:
donde ambos lo traen coinciden exactamente, BUK cubre más y está vivo.

**De `custom_attributes` solo se lee el apodo.** Ese campo también trae contacto
de emergencia con teléfono, restricción alimentaria, inclusión y nivel de
inglés; nada de eso cruza `chat/buk.py`.

Los nombres se muestran como **primer nombre, apodo entre comillas, resto**:

```
María "Mane" José Peña Gutiérrez
Javiera "Javi" Ignacia Moreno Soza
```

Si el apodo ya está en el nombre se omite: `Felipe Edwards Marin`, no
`Felipe "Felipe" Edwards Marin`. La comparación es por palabra completa, porque
"Javi" está dentro de "Javiera" pero es un apodo distinto que sí hay que mostrar.

Un campo puede traer varios (`Jose, JM` · `Ali o Alice`): se indexan todos para
buscar y se muestra el primero. Se indexan desde dos letras, porque `JM` y `Jo`
son apodos reales; las palabras cortas del idioma están en `RESERVADAS` para que
no disparen una búsqueda de persona en cualquier frase.

Si alguien no tiene apodo (15 de 98), se muestra el nombre completo sin más.

### Nombre ambiguo o no encontrado

`chat/personas.py::buscar` no adivina: si el nombre no coincide con nadie, o
coincide con varias personas (hay cinco "Javi" en la nómina), la herramienta
se lo dice a Gemini tal cual —`"encontrada": false`, con los candidatos si los
hay— y el modelo decide cómo pedir la aclaración, apoyado en el historial de
la conversación para entender la respuesta del usuario en el siguiente
mensaje. No hay una lista numerada ni un manejo especial de "responde con el
número 2" a nivel de código: eso, si ocurre, lo resuelve el modelo.

## Cuentas y clientes

`datos/Personas Hrs Sem x Cuenta.xlsx`, hoja *Detalle Cuenta-Persona*: 90
cuentas y 548 asignaciones. Se cruza con BUK por RUT. `listar_cuentas` expone
los 90 nombres tal cual, para "qué cuentas/clientes tenemos" sin nombrar una
en particular — `equipo_de` es para cuando sí se nombra una.

BUK tiene un campo `Cuentas` en `custom_attributes` pero está **vacío en los 98
empleados**, así que la planilla es la única fuente. Si algún día se llena en
BUK, conviene cambiar la fuente y dejar de depender del archivo.

Se reconoce la cuenta de tres formas, de más a menos específica: el nombre
completo tal cual (`aguas andinas`), una palabra que pertenece a una sola cuenta
(`santander` → `BANCO SANTANDER`), o —si la palabra está en varias (`AFP` está
en *AFP Capital* y *AFP Cuprum*)— preguntando cuál, igual que con los apodos.
Palabras como `banco` o `grupo` no identifican a ninguna: 35 de las 90 cuentas
tienen más de una palabra, y nadie dice "el equipo de BANCO SANTANDER".

**El RUT es solo la llave del cruce**: se usa para unir las dos fuentes y se
descarta antes de guardar el directorio. No queda almacenado ni sale en ninguna
respuesta, y hay un test que lo verifica.

La planilla no entra al corpus de documentos: `.xlsx` no está en `EXTENSIONES`
justamente porque trae RUTs y horas contractuales.

Si se pregunta por un grupo que no es ni área ni cuenta (`el equipo de
Santander`), el asistente lo dice y ofrece lo que sí tiene, en vez de responder
por toda la empresa ignorando el filtro.

## Beneficios

Módulo aparte de BUK, con permiso propio (sin él, `listar_beneficios` y
`beneficios_de_persona` fallan con un error de BUK, igual que cualquier otra
herramienta sin acceso). Dos endpoints, sin relación con nómina/ausencias:

- `/benefits/benefit_requests` — cada solicitud de un beneficio, con su
  estado (`approved`, `in_process`, `incomplete`, …) y **texto libre por
  solicitud** (`benefit_request_field_values`, `comments`,
  `cancel_comments`) que puede traer datos personales — una dirección para
  un permiso de mudanza, un motivo escrito a mano. Ese texto libre **no
  cruza `chat/buk.py`**, igual que el motivo de una licencia médica.
- `/benefits/benefit_versions/<id>` — el nombre de cada beneficio, de a uno
  por id. **No hay un endpoint para listar el catálogo completo.**

Por eso `listar_beneficios` en la práctica es "beneficios que alguien ya
solicitó alguna vez", no el catálogo teórico completo: se arma juntando los
`available_version_id` que aparecen en las solicitudes reales
(`chat/buk.py::beneficios`). Un beneficio definido en BUK pero que nadie
solicitó nunca no va a aparecer.

**El nombre de cada beneficio se cachea con un TTL más largo** (6 ×
`BUK_CACHE_TTL`) que las solicitudes: la primera vez que se pregunta por
beneficios, resolver el catálogo completo pide un request por cada
`available_version_id` distinto (16 en la nómina real) además de paginar las
solicitudes — bastante más lento que cualquier otra herramienta. Las
preguntas siguientes, mientras el caché siga vigente, son instantáneas.

**La paginación de este módulo usa `per_page`, no `page_size`** como el
resto de la API de BUK — es una inconsistencia real de BUK entre módulos, no
un typo de acá. Pedir `page_size` en este endpoint no rompe nada, pero BUK lo
ignora en silencio y pagina de a 25 en vez de 100, multiplicando por tres los
requests para traer las mismas ~70 solicitudes.

**BUK no expone en qué consiste cada beneficio, solo su nombre.** Lo
confirmé contra el spec oficial (`GET /apidocs`, `BenefitVersionResponse` en
`/api/chile/es/api_docs`): el único texto disponible es `name`
("Día libre por cumpleaños"); no hay condiciones, reglamento ni letra chica
en ningún campo. `INSTRUCCIONES` se lo deja explícito al modelo para que
responda `NO_SE` ante "en qué consiste X" en vez de inventar una descripción
a partir del nombre. Si se necesita ese detalle, tiene que venir de un
documento real (como `datos/politica_vacaciones.md`) que alguien cargue.

## Filtro por área

Las áreas salen de `/areas` y se cruzan con `current_job.area_id`:
Comunicaciones (37), Asuntos Públicos (15), Prensa (10), Administración (8),
Contenidos, Monitoreo, Diseño, Digital, Finanzas, IA, Operaciones, Personas,
Paid Media, Audiovisual.

Se detectan comparando con los nombres reales de BUK, no con una lista escrita a
mano: si crean un área nueva funciona sin tocar código.

**BUK no guarda la asignación por cliente** (`current_job.project` viene vacío):
`equipo_de` solo encuentra cuentas o áreas reales. Ante un grupo que no es
ninguna de las dos, la herramienta devuelve `"encontrado": false` y es Gemini
quien decide cómo explicarlo, en vez de responder por toda la empresa
ignorando el filtro.

### Cómo decide Gemini qué hacer

No hay un orden de resolución fijo en código: cada pregunta se manda a Gemini
con las ocho herramientas disponibles y es el modelo quien decide, turno a
turno, si responde directo (un saludo, una despedida), llama una herramienta,
o dice que no sabe con la marca `NO_SE:`. Ese criterio vive en `INSTRUCCIONES`
(`chat/asistente.py`), no en un router de código: cambiarlo es editar el
prompt, no reordenar funciones.

### Anonimizar la nómina

```bash
ASISTENTE_ANONIMIZAR=True
```

Reemplaza los nombres por alias (`Persona 1`, `Persona 2`) antes de enviarlos al
modelo y los restituye en la respuesta final. El proveedor ve la estructura de
la consulta pero no quién es quién.

**Lo que no protege:** el nombre que el propio usuario escribió en su pregunta
viaja igual dentro del mensaje. Si alguien pregunta "¿está Claudio de
vacaciones?", ese nombre llega al proveedor.

## Tipografía

**Lato**, la misma de azerta.cl.

Un detalle que conviene saber: Lato solo existe en **100, 300, 400, 700 y 900**.
azerta.cl declara pesos 500 y 800 que Google Fonts no entrega — el navegador los
falsifica estirando el 400 y el 700, y por eso el texto del sitio se ve algo
distinto según el navegador. Aquí se usan los reales: 400 para texto, 700 para
títulos y controles, 900 para el display de la mascota.

## Mascota

Es tipográfica, no una imagen: la palabra **Azerta⁷** centrada mientras el chat
está vacío. Al enviar el primer mensaje se va a la izquierda y las letras
`zerta` colapsan **en ancho**, no solo en opacidad, para que la `a` quede en su
lugar en vez de dejar un hueco. El resultado es que la palabra se convierte en
la **A** de Azerta.

Reacciona a la conversación con `data-estado`:

| Estado | Cuándo | Animación |
| --- | --- | --- |
| `reposo` | por defecto | flota suave |
| `pensando` | esperando la respuesta | se ladea, más rápido |
| `feliz` | respuesta correcta, o al hacerle clic | salto con aplaste y estiramiento |
| `apenado` | Gemini no disponible, o error de red | se encoge y se ladea hacia abajo |

Lleva `aria-hidden` y respeta `prefers-reduced-motion`. El PNG 3D que se usaba
antes quedó en `assets/mascota-3d.png` por si se quiere volver a él.

## Caché

Tres niveles, todos en la misma tabla de base de datos (`CACHE_BACKEND=locmem`
para volver a memoria, que se pierde en cada reinicio):

| Qué | TTL | Variable |
| --- | --- | --- |
| Respuesta completa a una pregunta | 600 s | `RESPUESTA_CACHE_TTL` |
| Directorio de personas | 600 s | `BUK_CACHE_TTL` |
| Una consulta de ausencias | 60 s | `BUK_ABSENCE_CACHE_TTL` |

La clave de una respuesta ignora acentos, mayúsculas, puntuación y espacios de
más, así que `¿Quién está fuera hoy?` y `quien esta fuera hoy` comparten
resultado. Incluye la fecha, porque la respuesta a "hoy" no sirve mañana.

Al cambiar cómo se arman las respuestas hay que subir `VERSION` en
`chat/respuestas.py`, o el caché sigue sirviendo el formato viejo.

### Precalentar

```bash
python manage.py precalentar --dias 7
```

Deja en caché el directorio y las ausencias de los próximos días. Puesto en un
cron temprano, ningún usuario espera la primera consulta del día.

### Qué se pregunta

```bash
python manage.py consultas --frecuentes
```

Muestra cada pregunta, cuántas veces se hizo, cuántas se sirvieron de caché y
cuántas resolvió el modelo. El porcentaje al final dice qué proporción no costó
nada.

## Login

Toda la app pide sesión iniciada — solo con una cuenta de Google del dominio
en `GOOGLE_WORKSPACE_DOMAIN` (`azerta.cl` por defecto). Sin usuario ni clave
propios: la única puerta es Google.

`chat/middleware.py::RequiereLoginMiddleware` es la barrera: si
`request.user` no está autenticado, redirige a `/accounts/login/`, salvo tres
prefijos (`chat/middleware.py::EXENTAS`):

- `/accounts/` — las propias URLs de login/OAuth (si estuvieran detrás del
  gate, nadie podría loguearse nunca).
- `/static/` — CSS/JS, para que la página de login se vea bien antes de que
  haya sesión.
- `/admin/` — tiene su **propio** login, de usuario y clave (el de
  `manage.py createsuperuser`), separado de Google a propósito: quien
  administra el backlog de preguntas no tiene por qué depender de una cuenta
  de Google, y forzar ese paso de más no suma nada.

**El filtro de dominio real es del lado del servidor**, no la pantalla de
Google. `chat/adapters.py::SoloAzertaSocialAdapter.pre_social_login` corta el
login si el email no termina en `@GOOGLE_WORKSPACE_DOMAIN`, devolviendo al
`/accounts/login/` con un aviso. El parámetro `hd` que Google le manda a su
propia pantalla (en `SOCIALACCOUNT_PROVIDERS` en `config/settings.py`) es
solo una sugerencia visual — preselecciona el dominio, pero se puede evitar
eligiendo otra cuenta ya logueada en el navegador — por eso hace falta la
barrera server-side además.

`chat/adapters.py::SoloAzertaAccountAdapter` cierra el registro local (con
usuario y clave): la única forma de crear una cuenta es a través de Google.

### Configurar las credenciales de Google (hay que crearlas una vez)

Sin esto, la app entera queda sin forma de iniciar sesión — nadie puede
entrar, ni siquiera para probarla.

1. Andá a [Google Cloud Console](https://console.cloud.google.com/) con una
   cuenta con acceso al Workspace de Azerta y creá un proyecto nuevo (o usá
   uno existente).
2. **Pantalla de consentimiento OAuth** (*APIs y servicios* → *Pantalla de
   consentimiento de OAuth*): tipo **Interno** (si el Workspace lo permite —
   así solo cuentas `@azerta.cl` pueden autorizar la app, una segunda capa
   además del filtro de `pre_social_login`) o **Externo** en modo prueba si
   no. Nombre de la app: "Iris". No hace falta pedir scopes más allá de
   `email` y `profile`, que son los que la app usa.
3. **Credenciales** → *Crear credenciales* → *ID de cliente de OAuth* → tipo
   **Aplicación web**.
4. **Orígenes de JavaScript autorizados**: la URL donde va a vivir Iris (por
   ejemplo `https://iris.azerta.cl`, o `http://localhost:8000` para probar
   en local).
5. **URI de redirección autorizados** — esta parte tiene que quedar exacta,
   letra por letra, o Google rechaza el login:
   `https://iris.azerta.cl/accounts/google/login/callback/`
   (cambiando el dominio por donde corresponda; en local,
   `http://localhost:8000/accounts/google/login/callback/`).
6. Google te muestra un **Client ID** y un **Client Secret**. Van en `.env`:
   ```bash
   GOOGLE_OAUTH_CLIENT_ID=...
   GOOGLE_OAUTH_CLIENT_SECRET=...
   GOOGLE_WORKSPACE_DOMAIN=azerta.cl
   ```
7. `python manage.py migrate` (si no se corrió ya) y reiniciar el servidor.

Sin `GOOGLE_OAUTH_CLIENT_ID`/`SECRET`, el botón "Continuar con Google" lleva
a un error real de Google ("Missing required parameter: client_id") — es
justamente cómo se ve mientras falta este paso, no un bug de la app.

### Cómo se ve el login sin las credenciales todavía puestas

Ya está probado en vivo que la parte de la app funciona: `/` redirige a
`/accounts/login/`, la página se ve con el estilo de Iris y pide el dominio
correcto, y al hacer clic en "Continuar con Google" se llega hasta
`accounts.google.com` de verdad (ahí rebota, sin credenciales reales, con el
error de arriba). Falta el paso 6 de la lista — las credenciales— para que el
login se complete.
