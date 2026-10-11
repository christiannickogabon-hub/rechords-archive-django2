// "Endorsement level" only applies to endorsements; hide it for other types.
(function () {
  var type = document.querySelector("#document-form [name='doc_type']");
  var level = document.getElementById("endorsement-level");
  if (!type || !level) return;

  function sync() {
    level.hidden = type.value !== "IND";
  }
  type.addEventListener("change", sync);
  sync();
})();
