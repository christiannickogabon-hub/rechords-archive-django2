// Sign-in pages: show a round spinner, slide the card out, then post the form.
(function () {
  var screen = document.querySelector(".signin");
  var form = screen && screen.querySelector("form[method='post']");
  if (!form) return;

  var spinner = document.querySelector(".auth-spinner");
  var reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var leaving = false;

  form.addEventListener("submit", function (event) {
    if (leaving) return;
    leaving = true;
    if (spinner) spinner.hidden = false;

    if (reduced) return;            // let it post immediately, spinner still showing
    event.preventDefault();
    screen.classList.add("is-leaving");
    setTimeout(function () { form.submit(); }, 380);
  });
})();
