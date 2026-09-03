# Documentos del asistente

Cualquier archivo `.md` o `.txt` que dejes en esta carpeta queda disponible para
el asistente. No hay que reiniciar el servidor: los cambios se detectan por
fecha de modificación.

## Cómo se busca

Cada archivo se parte por títulos (`#`, `##`, `###`) y cada sección se compara
con las palabras de la pregunta. Gana la sección con más palabras en común, y el
título pesa doble.

Por eso conviene **titular las secciones con las palabras que la gente usaría al
preguntar**. Un título como "Cómo pedir vacaciones" funciona mejor que
"Procedimiento 4.2".

## Límites

La búsqueda es léxica, no semántica: encuentra la sección cuando la pregunta
comparte palabras con ella. Si alguien pregunta "¿cuántos días me tocan?" y el
documento dice "feriado legal", no hay coincidencia. Un modelo de lenguaje
resolvería eso, y usaría estas mismas secciones como fuente.
