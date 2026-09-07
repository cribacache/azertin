const form = document.querySelector("#chat-form");
const input = document.querySelector("#message-input");
const boton = document.querySelector("#send-button");
const messages = document.querySelector("#messages");
const vacio = document.querySelector("#empty-state");
const csrftoken = document.querySelector("[name=csrfmiddlewaretoken]").value;
const mascota = document.querySelector("#mascota");
const palabra = document.querySelector("#mascota .palabra");

/* La mascota es la palabra "azerta". Al empezar la conversación se va a la
   izquierda y se queda solo en la "a". Es decorativa: si no está, nada falla. */
let animando = null;

if (palabra) {
  palabra.style.pointerEvents = "auto";
  palabra.style.cursor = "pointer";
  palabra.addEventListener("click", () => estadoMascota("feliz", 800));
}

function estadoMascota(estado, volverEn = 0) {
  if (!mascota || !mascota.isConnected) return;
  clearTimeout(animando);
  // reiniciar la animación: sin esto, dos alegrías seguidas no se notan
  mascota.dataset.estado = "reposo";
  void mascota.offsetWidth;
  mascota.dataset.estado = estado;
  if (volverEn) {
    animando = setTimeout(() => { mascota.dataset.estado = "reposo"; }, volverEn);
  }
}

function acompanarConversacion() {
  if (mascota && mascota.isConnected) mascota.classList.add("acompana");
}

const hora = () =>
  new Date().toLocaleTimeString("es-CL", { hour: "2-digit", minute: "2-digit", hour12: false });

function alFinal() {
  messages.scrollTop = messages.scrollHeight;
}

function quitarVacio() {
  if (vacio && vacio.isConnected) {
    vacio.remove();
    acompanarConversacion();
  }
}

function addMessage(text, type, items = null, meta = null) {
  quitarVacio();
  const item = document.createElement("div");
  item.className = `message ${type}`;
  item.innerHTML = `<span class="avatar"></span><div><p></p><time></time></div>`;
  item.querySelector(".avatar").textContent = type === "user" ? "T" : "a";
  item.querySelector("p").textContent = text;
  item.querySelector("time").textContent = hora();
  const cuerpo = item.querySelector("div");

  if (items && items.length) {
    const lista = document.createElement("ul");
    lista.className = "people";
    for (const p of items) {
      const li = document.createElement("li");
      const rango = p.hasta && p.hasta !== p.desde ? `${p.desde} a ${p.hasta}` : p.desde;
      const dias = p.dias ? ` · ${p.dias} ${p.dias === 1 ? "día" : "días"}` : "";
      const media = p.media_jornada ? " · media jornada" : "";
      const pend = p.estado === "pendiente" ? " · pendiente" : "";
      li.innerHTML = `<span class="top"><strong></strong><em class="kind"></em></span>` +
                     `<span class="role"></span><span class="dates"></span>`;
      li.querySelector("strong").textContent = p.nombre;
      const kind = li.querySelector(".kind");
      kind.textContent = p.detalle ? `${p.tipo} · ${p.detalle}` : p.tipo;
      kind.dataset.cat = p.tipo;
      li.dataset.cat = p.tipo;
      li.querySelector(".role").textContent = p.cargo;
      li.querySelector(".dates").textContent = `${rango}${dias}${media}${pend}`;
      lista.appendChild(li);
    }
    cuerpo.appendChild(lista);
  }

  if (meta && meta.requests_buk !== undefined) {
    const tag = document.createElement("span");
    tag.className = "meta-tag";
    const partes = [];
    if (meta.desde_cache) partes.push("desde caché");
    else partes.push(`${meta.requests_buk} consulta${meta.requests_buk === 1 ? "" : "s"} a BUK`);
    if (meta.intencion === "modelo") partes.push("con modelo");
    if (meta.parcial) partes.push("respuesta de respaldo");
    if (meta.seccion) partes.push(meta.seccion);
    if (meta.parcial) tag.dataset.respaldo = "1";
    tag.textContent = partes.join(" · ");
    cuerpo.appendChild(tag);
  }

  messages.appendChild(item);
  alFinal();
  return item;
}

function mostrarEscribiendo() {
  quitarVacio();
  const item = document.createElement("div");
  item.className = "message bot";
  item.innerHTML = `<span class="avatar">a</span><div><div class="typing" role="status" ` +
                   `aria-label="Escribiendo"><i></i><i></i><i></i></div></div>`;
  messages.appendChild(item);
  messages.setAttribute("aria-busy", "true");
  alFinal();
  return item;
}

async function checkConnection() {
  const title = document.querySelector("#connection-title");
  const detail = document.querySelector("#connection-detail");
  const dot = document.querySelector("#signal-dot");
  try {
    const response = await fetch("/api/status/");
    const result = await response.json();
    if (!response.ok) throw new Error(result.error);
    dot.classList.add("ok");
    title.textContent = "En línea";
    detail.textContent = `${result.personas_activas} personas`;
  } catch (error) {
    dot.classList.add("error");
    title.textContent = "Sin conexión";
    detail.textContent = error.message ? "" : "";
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = input.value.trim();
  if (!message || boton.disabled) return;

  addMessage(message, "user");
  input.value = "";
  boton.disabled = true;
  estadoMascota("pensando");
  const escribiendo = mostrarEscribiendo();

  try {
    const response = await fetch("/api/chat/", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrftoken },
      body: JSON.stringify({ message }),
    });
    const result = await response.json();
    escribiendo.remove();
    if (!response.ok) throw new Error(result.error);
    addMessage(result.answer, "bot", result.items, result.meta);
    // apenada si tuvo que responder de respaldo, contenta si salió bien
    estadoMascota(result.meta && result.meta.parcial ? "apenado" : "feliz", 2200);
  } catch (error) {
    escribiendo.remove();
    estadoMascota("apenado", 3000);
    addMessage(
      error.message || "No pude completar la consulta. Inténtalo otra vez en unos segundos.",
      "bot",
    );
  } finally {
    messages.setAttribute("aria-busy", "false");
    boton.disabled = false;
    input.focus();
  }
});

checkConnection();
