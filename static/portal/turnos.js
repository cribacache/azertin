// Portal de turnos (/rrhh/turnos/).
//
// Dos cosas separadas:
// 1) iniciarSelects(): reemplaza cada <select> por un boton + un panel
//    animado compartido (estilo "hoja" de iOS, con la paleta de Iris). El
//    <select> real sigue en el DOM, oculto, y es lo que se manda en el
//    <form> -el panel solo le cambia el value y dispara "change".
// 2) ajustarModalidad(): que opciones de Modalidad tiene sentido ofrecer
//    segun la Forma de trabajo elegida. Nada de esto reemplaza la
//    validacion real (Turno.clean(), chat/models.py): si alguien la evita
//    (JS deshabilitado, form editado a mano) el server igual manda.

function iniciarSelects() {
  const panel = document.createElement("div");
  panel.className = "iris-select-panel";
  panel.setAttribute("role", "listbox");
  document.body.appendChild(panel);

  let selectAbierto = null;

  const etiquetaDe = (select) => {
    const opcion = select.options[select.selectedIndex];
    return opcion ? opcion.textContent : "";
  };

  const refrescarTexto = (select) => {
    if (select._textoTrigger) select._textoTrigger.textContent = etiquetaDe(select);
  };

  const cerrar = () => {
    panel.classList.remove("abierto");
    if (selectAbierto && selectAbierto._trigger) {
      selectAbierto._trigger.setAttribute("aria-expanded", "false");
      selectAbierto._trigger.classList.remove("activo");
    }
    selectAbierto = null;
  };

  const abrir = (select) => {
    if (selectAbierto === select) { cerrar(); return; }
    selectAbierto = select;
    panel.innerHTML = "";

    Array.from(select.options).forEach((opcion) => {
      if (opcion.hidden || opcion.disabled) return;
      const item = document.createElement("button");
      item.type = "button";
      item.className = "iris-select-opcion";
      item.setAttribute("role", "option");
      if (opcion.value === select.value) item.classList.add("seleccionada");
      item.textContent = opcion.textContent;
      item.addEventListener("click", () => {
        if (select.value !== opcion.value) {
          select.value = opcion.value;
          select.dispatchEvent(new Event("change", { bubbles: true }));
        }
        refrescarTexto(select);
        cerrar();
      });
      panel.appendChild(item);
    });

    const r = select._trigger.getBoundingClientRect();
    const alto = Math.min(260, window.innerHeight - r.bottom - 16);
    panel.style.left = `${Math.max(8, r.left)}px`;
    panel.style.top = `${r.bottom + 6}px`;
    panel.style.minWidth = `${r.width}px`;
    panel.style.maxHeight = `${Math.max(120, alto)}px`;

    select._trigger.setAttribute("aria-expanded", "true");
    select._trigger.classList.add("activo");
    requestAnimationFrame(() => panel.classList.add("abierto"));
  };

  const crearTrigger = (select) => {
    const envoltura = document.createElement("div");
    envoltura.className = "iris-select";
    select.classList.add("iris-select-real");
    select.setAttribute("tabindex", "-1");
    select.setAttribute("aria-hidden", "true");

    const boton = document.createElement("button");
    boton.type = "button";
    boton.className = "iris-select-trigger";
    boton.setAttribute("aria-haspopup", "listbox");
    boton.setAttribute("aria-expanded", "false");

    const texto = document.createElement("span");
    texto.className = "iris-select-texto";
    texto.textContent = etiquetaDe(select);

    const flecha = document.createElement("span");
    flecha.className = "iris-select-flecha";
    flecha.setAttribute("aria-hidden", "true");

    boton.append(texto, flecha);
    select.parentNode.insertBefore(envoltura, select);
    envoltura.append(select, boton);

    select._trigger = boton;
    select._textoTrigger = texto;
    boton.addEventListener("click", () => abrir(select));
  };

  document.addEventListener("click", (evento) => {
    if (!selectAbierto) return;
    if (panel.contains(evento.target) || selectAbierto._trigger.contains(evento.target)) return;
    cerrar();
  });
  document.addEventListener("keydown", (evento) => {
    if (evento.key === "Escape") cerrar();
  });
  window.addEventListener("scroll", () => { if (selectAbierto) cerrar(); }, true);
  window.addEventListener("resize", cerrar);

  document
    .querySelectorAll(".campo-departamento, .campo-forma-trabajo, .campo-modalidad")
    .forEach(crearTrigger);

  return { refrescarTexto };
}

const MODALIDADES_PERMITIDAS = {
  permanente: ["presencial"],
  hibrido: ["turno_1", "turno_2"],
  transitorio: ["acuerdo", "conciliacion_familiar"],
};

function iniciarDependenciaModalidad(selects) {
  document.querySelectorAll(".campo-forma-trabajo").forEach((selectFormaTrabajo) => {
    const fila = selectFormaTrabajo.closest("tr");
    const selectModalidad = fila ? fila.querySelector(".campo-modalidad") : null;
    if (!selectModalidad) return;

    const aplicar = () => {
      const permitidas = MODALIDADES_PERMITIDAS[selectFormaTrabajo.value] || null;
      let quedaAlguna = false;
      for (const opcion of selectModalidad.options) {
        const visible = !permitidas || permitidas.includes(opcion.value);
        opcion.hidden = !visible;
        opcion.disabled = !visible;
        if (visible) quedaAlguna = true;
      }
      if (permitidas && quedaAlguna && !permitidas.includes(selectModalidad.value)) {
        selectModalidad.value = permitidas[0];
        selectModalidad.dispatchEvent(new Event("change", { bubbles: true }));
      }
      selects.refrescarTexto(selectModalidad);
    };

    selectFormaTrabajo.addEventListener("change", aplicar);
    aplicar();
  });
}

document.addEventListener("DOMContentLoaded", () => {
  const selects = iniciarSelects();
  iniciarDependenciaModalidad(selects);

  document.querySelectorAll(".turno-eliminar-form").forEach((form) => {
    form.addEventListener("submit", (evento) => {
      if (!confirm("¿Eliminar este turno?")) evento.preventDefault();
    });
  });
});
