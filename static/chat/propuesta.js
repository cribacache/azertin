// La pagina de propuestas se abre en una pestana nueva para no perder la
// conversacion del chat. Al terminar, si el navegador deja cerrar la pestana
// (la abrio un clic, no el usuario a mano) se cierra; si no, se vuelve al chat
// en la misma pestana como respaldo.
//
// Va en un archivo aparte, no inline, para que la CSP pueda prohibir scripts
// inline (ver chat/middleware.py::SecurityHeadersMiddleware).
(function () {
  var boton = document.getElementById("cerrar-pestana");
  if (!boton) return;
  boton.addEventListener("click", function () {
    window.close();
    var destino = boton.dataset.chatUrl || "/";
    setTimeout(function () { location.href = destino; }, 200);
  });
})();
