// Live checking for Philippine contact numbers; mirrors PHContactNumberField
// in accounts/forms.py. Extra digits are refused as they are typed, so a number
// can never grow past 11 (0917 123 4567) or 12 with the 63 country code.
(function () {
  var LIMITS = { "63": 12, "0": 11, "": 10 };

  function spelling(digits) {
    if (digits.indexOf("63") === 0) return "63";
    if (digits.indexOf("0") === 0) return "0";
    return "";
  }

  function allowed(digits) {
    return LIMITS[spelling(digits)];
  }

  function body(digits) {
    var prefix = spelling(digits);
    return digits.slice(prefix.length);
  }

  function state(value) {
    var digits = value.replace(/\D/g, "");
    if (!digits) return { ok: true, digits: digits };
    if (digits.length > allowed(digits)) return { ok: false, why: "long", digits: digits };
    var rest = body(digits);
    if (rest.length < 10) return { ok: false, why: "short", digits: digits };
    if (rest.length > 10 || rest.charAt(0) !== "9") return { ok: false, why: "invalid", digits: digits };
    return { ok: true, digits: digits };
  }

  var MESSAGES = {
    long: "That is too long. A Philippine number has 11 digits (0917 123 4567), or 12 with the 63 country code.",
    short: "That is too short. A Philippine number has 11 digits (0917 123 4567), or 12 with the 63 country code.",
    invalid: "Invalid Philippine number. Use 11 digits like 0917 123 4567, or +63 917 123 4567."
  };

  /* Refuse the surplus rather than letting it be typed and complaining later. */
  function capLength(input) {
    var digits = input.value.replace(/\D/g, "");
    var max = allowed(digits);
    if (digits.length <= max) return false;

    var kept = 0;
    var out = "";
    for (var i = 0; i < input.value.length; i += 1) {
      var ch = input.value.charAt(i);
      if (/\d/.test(ch)) {
        if (kept >= max) continue;
        kept += 1;
      }
      out += ch;
    }
    input.value = out;
    return true;
  }

  function check(input, warning, force) {
    var result = state(input.value.trim());
    var show = !result.ok && (force || result.why === "long");
    input.classList.toggle("is-invalid", show);
    input.setAttribute("aria-invalid", show ? "true" : "false");
    warning.textContent = MESSAGES[result.why] || MESSAGES.invalid;
    warning.hidden = !show;
    return result.ok;
  }

  document.querySelectorAll("input[data-ph-contact]").forEach(function (input) {
    var warning = document.createElement("div");
    warning.className = "field-warning";
    warning.id = (input.id || "contact") + "-warning";
    warning.setAttribute("role", "alert");
    warning.hidden = true;
    input.insertAdjacentElement("afterend", warning);
    input.setAttribute("aria-describedby", warning.id);

    var touched = false;

    input.addEventListener("input", function () {
      var trimmed = capLength(input);
      if (trimmed) {
        touched = true;
        warning.textContent = MESSAGES.long;
        warning.hidden = false;
        input.classList.add("is-invalid");
      } else if (touched) {
        check(input, warning, true);
      }
    });

    input.addEventListener("blur", function () {
      touched = true;
      check(input, warning, true);
    });

    input.form && input.form.addEventListener("submit", function (e) {
      touched = true;
      if (!check(input, warning, true)) {
        e.preventDefault();
        input.focus();
      }
    });
  });
}());
