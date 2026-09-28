// Portal de turnos (/rrhh/turnos/). Nada de esto reemplaza la validacion
// real: Turno.clean() (chat/models.py) manda igual si alguien evita esto
// (JS deshabilitado, form editado a mano). Esto es solo para que el select
// de modalidad no ofrezca combinaciones invalidas.

const MODALIDADES_PERMITIDAS = {
  permanente: ["presencial"],
  hibrido: ["turno_1", "turno_2"],
  transitorio: ["acuerdo", "conciliacion_familiar"],
};

function ajustarModalidad(selectFormaTrabajo) {
  const form = selectFormaTrabajo.closest("form");
  if (!form) return;
  const selectModalidad = form.querySelector(".campo-modalidad");
  if (!selectModalidad) return;

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
  }
}

document.querySelectorAll(".campo-forma-trabajo").forEach((select) => {
  ajustarModalidad(select);
  select.addEventListener("change", () => ajustarModalidad(select));
});

document.querySelectorAll(".turno-eliminar-form").forEach((form) => {
  form.addEventListener("submit", (evento) => {
    if (!confirm("¿Eliminar este turno?")) evento.preventDefault();
  });
});
