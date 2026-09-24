const form = document.querySelector("#chat-form");
const input = document.querySelector("#message-input");
const boton = document.querySelector("#send-button");
const messages = document.querySelector("#messages");
const vacio = document.querySelector("#empty-state");
const csrftoken = document.querySelector("[name=csrfmiddlewaretoken]").value;
const mascota = document.querySelector("#mascota");
const palabra = document.querySelector("#mascota .palabra");

/* La mascota es la palabra "Azerta". Al empezar la conversación se va a la
   izquierda y se queda solo en la "A". Es decorativa: si no está, nada falla. */
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

function addMessage(text, type, items = null, salas = null, contactos = null) {
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

  // Disponibilidad de salas (salas_disponibles, ver chat/asistente.py): la
  // ocupada se tacha, aparte de lo que Iris haya redactado en el texto.
  if (salas && salas.length) {
    const lista = document.createElement("ul");
    lista.className = "salas";
    for (const s of salas) {
      const li = document.createElement("li");
      li.className = s.ocupada ? "ocupada" : "libre";
      li.innerHTML = `<span class="sala-nombre"></span><span class="sala-estado"></span>`;
      li.querySelector(".sala-nombre").textContent = s.sala;
      li.querySelector(".sala-estado").textContent = s.ocupada ? "Ocupada" : "Libre";
      lista.appendChild(li);
    }
    cuerpo.appendChild(lista);
  }

  // Tarjetas de contacto (buscar_contactos, Azerta Finder): aparte del
  // texto, una por cada contacto encontrado (puede ser mas de uno, ej.
  // "los contactos de tal organizacion"), con todos los datos que haya
  // aunque solo hayan pedido el telefono (ver chat/asistente.py).
  if (contactos && contactos.length) {
    const lista = document.createElement("div");
    lista.className = "contactos";
    for (const contacto of contactos) {
      const tarjeta = document.createElement("div");
      tarjeta.className = "contacto";
      const puesto = [contacto.cargo, contacto.organizacion].filter(Boolean).join(" · ");
      tarjeta.innerHTML =
        `<strong class="contacto-nombre"></strong>` +
        (puesto ? `<span class="contacto-puesto"></span>` : "") +
        `<dl class="contacto-datos"></dl>`;
      tarjeta.querySelector(".contacto-nombre").textContent = contacto.nombre;
      if (puesto) tarjeta.querySelector(".contacto-puesto").textContent = puesto;
      const datos = tarjeta.querySelector(".contacto-datos");
      const campo = (etiqueta, valor) => {
        if (!valor) return;
        const dt = document.createElement("dt");
        dt.textContent = etiqueta;
        const dd = document.createElement("dd");
        dd.textContent = valor;
        datos.append(dt, dd);
      };
      campo("Teléfono", contacto.telefono);
      campo("Mail", contacto.mail);
      lista.appendChild(tarjeta);
    }
    cuerpo.appendChild(lista);
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

// Al lado del logo: solo la luz, sin texto. Que modelo esta respondiendo
// ahora y por que (si Gemini no esta disponible) queda en el title del
// contenedor, visible al pasar el mouse.
//
// `nuevaConversacion` solo va en true en la carga inicial de la pagina: ese
// es el unico momento en que hay que olvidar la conversacion anterior. Este
// mismo chequeo se vuelve a llamar despues de CADA mensaje (mas abajo) para
// refrescar la luz; si tambien borrara el historial ahi, ninguna
// conversacion de mas de un mensaje podria mantener el contexto (bug real:
// se perdia todo lo hablado apenas llegaba la primera respuesta).
async function checkConnection(nuevaConversacion = false) {
  const modelDot = document.querySelector("#model-signal-dot");
  const modelPill = document.querySelector("#model-signal");

  try {
    const url = nuevaConversacion ? "/api/status/?nueva=1" : "/api/status/";
    const response = await fetch(url);
    const result = await response.json();
    if (!response.ok) throw new Error(result.error);

    const asistente = result.asistente;
    if (asistente) {
      modelDot.classList.remove("ok", "off");
      if (asistente.disponible) {
        modelDot.classList.add("ok");
        modelPill.title = `Respondiendo con ${asistente.modelo}.`;
      } else {
        modelDot.classList.add("off");
        modelPill.title =
          `${asistente.modelo}: ${asistente.motivo_legible}. ` +
          "El chat avisa el error en vez de responder mientras tanto.";
      }
    }
  } catch (error) {
    modelDot.classList.remove("ok", "off");
    modelPill.title = "Sin conexión con el servidor.";
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
    addMessage(result.answer, "bot", result.items, result.salas, result.contactos);
    // apenada si Gemini no estaba disponible, contenta si salió bien
    const noDisponible = result.meta && result.meta.intencion === "no_disponible";
    estadoMascota(noDisponible ? "apenado" : "feliz", 2200);
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
    // Refresca el indicador de modelo: si esta pregunta agoto la cuota, que
    // se note al tiro y no recien cuando se recargue la pagina.
    checkConnection();
  }
});

checkConnection(true);
