# azertin

Asistente interno de Azerta. Centraliza la información operacional de la empresa
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

Abre `http://127.0.0.1:8000/`.

Para probar los tres formatos de autenticación sin mostrar la clave:

```bash
python scripts/check_buk_auth.py
```

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

El router de `chat/intents.py` traduce la pregunta a una categoría y un rango, y
`chat/buk.py` la resuelve contra la fuente que corresponda. Ejemplos:

| Pregunta | Fuente |
| --- | --- |
| ¿Quién está fuera hoy? | `/vacations` + `/absences`, combinadas |
| ¿Quién está de vacaciones hoy? | `/vacations` |
| Días administrativos en septiembre | `/vacations`, subtipo `dias_administrativos` |
| Licencias médicas esta semana | `/absences?type=licence` |
| ¿Quién faltó ayer? | `/absences?type=absence` |
| ¿Cuántas personas hay activas? | solo el directorio cacheado |

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

Si la pregunta nombra a alguien de la nómina, el asistente responde en texto
sobre esa persona en vez de listar a todo el mundo:

```
¿Claudio Lizama está con licencia?
→ Claudio Lizama Espinoza tiene licencia medica del 25 de agosto al 8 de
  septiembre (Director/a - Comunicaciones).

¿Elisa está con licencia hoy?
→ Elisa Eliana Palomino Marchant no registra ausencias hoy. Segun BUK, esta en
  su jornada.
```

El nombre se resuelve contra el directorio en `chat/personas.py`. Basta el
nombre o el apellido (`¿cuándo vuelve Duk?`). Si coincide con varias personas,
lo dice y pide el apellido en vez de adivinar.

## Documentos internos

Cualquier `.md` o `.txt` en `datos/` queda disponible para el asistente, sin
reiniciar el servidor. Los archivos que empiezan con `LEEME` o `README` se
ignoran: son documentación del repo, no contenido consultable.

**El bot no "aprende" el documento**: no hay entrenamiento. El archivo se lee
cuando llega la pregunta y se le entrega la sección pertinente. Editarlo cambia
las respuestas en el acto, y borrarlo las quita.

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

Al modelo se le entregan las **tres** mejores secciones, no una: una pregunta
suele cruzar dos, y componer es lo que el modelo hace bien.

Las preguntas de procedimiento se enrutan a los documentos aunque mencionen
palabras de BUK: `¿cómo pido vacaciones?` responde con la política, no con la
lista de quién está de vacaciones.

## Preguntas que no supo responder

Cuando no puede responder, el asistente lo dice y guarda la pregunta:

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

## Confidencialidad

Además de los datos que nunca salen del backend (RUT, dirección, cuenta
bancaria, previsión), el **motivo de una licencia médica** (`licence_type`: pre
natal, accidente común, etc.) se descarta en `chat/buk.py` al normalizar el
registro. Es información de salud y no entra a la aplicación.

## Respaldo con modelo de lenguaje (opcional)

Sin clave, la aplicación funciona igual: responde con reglas y dice "no cuento
con esa información" para el resto. Con clave, lo que las reglas no entienden
pasa a un modelo.

Hay dos proveedores. Lo único que cambia entre ellos es el bucle de llamadas en
`chat/asistente.py`: las herramientas, el prompt y la anonimización son los
mismos.

```bash
# en .env — Gemini (tiene capa gratuita)
ASISTENTE_PROVEEDOR=gemini
GEMINI_API_KEY=...
GEMINI_MODEL=gemini-2.0-flash

# o bien OpenAI (requiere saldo, no tiene capa gratuita)
ASISTENTE_PROVEEDOR=openai
OPENAI_API_KEY=sk-...
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

**Al modelo no se le entrega la API de BUK.** Solo puede llamar a las funciones
declaradas en `chat/herramientas.py`, que reutilizan la misma capa sanitizada
que usa el router:

| Herramienta | Qué hace |
| --- | --- |
| `listar_ausencias` | quién no está en su jornada en un rango |
| `ausencias_de_persona` | situación de una persona (resuelve el nombre localmente) |
| `dotacion` | cantidad de personas activas |
| `buscar_politica` | busca en los documentos de `datos/` |

La clave de BUK nunca sale del backend, el modelo no ve el payload crudo, y el
motivo de una licencia médica ya viene descartado desde `chat/buk.py`.

### Cómo se combinan las reglas y el modelo

El modelo no reemplaza al router: se apoyan mutuamente.

- **Las reglas le adelantan los datos.** Antes de llamar al modelo, el router
  resuelve el rango de fechas y consulta BUK, y le pasa el resultado en el
  mensaje. Así el modelo responde en una sola llamada en vez de gastar una
  vuelta pidiendo con una herramienta lo que ya teníamos.
- **Las reglas quedan de red.** Esa misma consulta se guarda como respaldo. Si
  el modelo falla o se demora, se entrega la respuesta de las reglas marcada
  como parcial, en vez de un "no tengo esa información".
- **Cortacircuitos.** Tras `ASISTENTE_FALLAS_MAX` fallas seguidas (3), se deja
  de llamar al proveedor por `ASISTENTE_PAUSA_SEGUNDOS` (180) y responden solo
  las reglas, al instante. Una respuesta exitosa lo reactiva.

El contexto adelantado pasa por la misma anonimización que los resultados de las
herramientas; si no, sería un atajo que la burla.

### Orden de resolución

1. Cortesía (saludos, gracias, despedidas, "¿quién eres?") — instantáneo, sin
   BUK y sin modelo. Un saludo no necesita datos: gastarle una llamada al
   proveedor cuesta cuota y segundos, y si está caído termina respondiendo
   "no tengo esa información" a un "Hola".
2. Router de reglas — instantáneo, sin tokens. Cubre las preguntas frecuentes.
3. Persona nombrada en la pregunta.
4. Documentos de `datos/`.
5. Modelo de lenguaje.
6. Respaldo de las reglas si el modelo falla; si tampoco hay, "todavía no tengo
   esa información" + registro en `manage.py consultas`.

La cortesía se evalúa **al final** del router, no al principio: así "hola,
¿quién está fuera hoy?" se responde como la consulta que es, y no como un
saludo.

Como el modelo es el último recurso, solo paga tokens la cola larga. Si falla o
se cae la API, la aplicación responde igual con el paso 5.

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

## Preguntas que las reglas no deben contestar

Comparaciones, agregaciones, filtros por área o causas (`¿qué área tiene más
ausencias?`, `compara agosto con septiembre`) son preguntas que el router
reconocería a medias y contestaría con una lista equivocada. `intents.es_compleja`
las detecta y las manda al modelo; sin modelo configurado, el bot dice que no
puede y registra la pregunta. Una respuesta incorrecta con formato correcto es
peor que ninguna.

## Pendiente

La app todavía no tiene autenticación: `/api/status/` y `/api/chat/` responden a
cualquiera que alcance el servidor. Hay que resolverlo antes de exponerla fuera
de localhost.
