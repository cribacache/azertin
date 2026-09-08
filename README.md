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

## Confidencialidad

Además de los datos que nunca salen del backend (RUT, dirección, cuenta
bancaria, previsión), el **motivo de una licencia médica** (`licence_type`: pre
natal, accidente común, etc.) se descarta en `chat/buk.py` al normalizar el
registro. Es información de salud y no entra a la aplicación.

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
| `dotacion` | cantidad de personas activas |
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

La herramienta `cumpleanos(desde, dias)` devuelve a quién le toca dentro de ese
rango; ampliar el rango cuando nadie cumple justo esa fecha ("mostrar los
próximos") ya no es un comportamiento fijo del código, sino algo que Gemini
puede decidir hacer llamando la herramienta de nuevo con más días.

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

`¿quién está trabajando hoy?`, `¿está todo el equipo?`. No hay una herramienta
dedicada a esto: Gemini lo calcula combinando `dotacion` (el total) con
`listar_ausencias` (quién no está), y arma la lista o el resumen según lo que
se haya preguntado. Es una diferencia real frente al router de reglas que
había antes — ahí el cálculo y el formato de la respuesta eran fijos; ahora
depende de que el modelo razone bien con los datos de ambas herramientas.

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
cuentas y 548 asignaciones. Se cruza con BUK por RUT.

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

## Pendiente

La app todavía no tiene autenticación: `/api/status/` y `/api/chat/` responden a
cualquiera que alcance el servidor. Hay que resolverlo antes de exponerla fuera
de localhost.
