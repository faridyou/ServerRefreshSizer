// RefreshSizer UI helpers (vanilla JS, no build step). Pages also work without JavaScript.
(function () {
  function sortTable(th) {
    var table = th.closest("table"), idx = Array.prototype.indexOf.call(th.parentNode.children, th);
    var body = table.tBodies[0], rows = Array.prototype.slice.call(body.rows);
    var asc = th.getAttribute("data-dir") !== "asc";
    rows.sort(function (a, b) {
      var x = a.cells[idx].getAttribute("data-v") || a.cells[idx].innerText;
      var y = b.cells[idx].getAttribute("data-v") || b.cells[idx].innerText;
      var nx = parseFloat(String(x).replace(/[$,%\s]/g, "")), ny = parseFloat(String(y).replace(/[$,%\s]/g, ""));
      var c = (!isNaN(nx) && !isNaN(ny)) ? nx - ny : String(x).localeCompare(String(y));
      return asc ? c : -c;
    });
    rows.forEach(function (r) { body.appendChild(r); });
    th.setAttribute("data-dir", asc ? "asc" : "desc");
  }
  document.addEventListener("click", function (e) {
    var th = e.target.closest("th.sort"); if (th) { sortTable(th); }
    var cp = e.target.closest("[data-copy]");
    if (cp) {
      var src = document.getElementById(cp.getAttribute("data-copy"));
      var text = src.innerText;
      if (navigator.clipboard) { navigator.clipboard.writeText(text); }
      else { var ta = document.createElement("textarea"); ta.value = text; document.body.appendChild(ta); ta.select();
             document.execCommand("copy"); document.body.removeChild(ta); }
      cp.innerText = cp.getAttribute("data-done") || "Copied";
    }
  });
  document.addEventListener("input", function (e) {
    var f = e.target.closest("[data-filter]"); if (!f) return;
    var table = document.getElementById(f.getAttribute("data-filter")), q = f.value.toLowerCase();
    Array.prototype.forEach.call(table.tBodies[0].rows, function (r) {
      r.style.display = r.innerText.toLowerCase().indexOf(q) >= 0 ? "" : "none";
    });
  });
  function initDrop() {
    var zone = document.getElementById("drop"); if (!zone) return;
    var input = document.getElementById("fileinput"), form = zone.closest("form");
    zone.addEventListener("click", function () { input.click(); });
    ["dragenter", "dragover"].forEach(function (ev) { zone.addEventListener(ev, function (e) {
      e.preventDefault(); zone.classList.add("over"); }); });
    ["dragleave", "drop"].forEach(function (ev) { zone.addEventListener(ev, function (e) {
      e.preventDefault(); zone.classList.remove("over"); }); });
    zone.addEventListener("drop", function (e) { input.files = e.dataTransfer.files; form.requestSubmit(); });
    input.addEventListener("change", function () { if (input.files.length) form.requestSubmit(); });
  }
  document.addEventListener("DOMContentLoaded", initDrop);
  document.addEventListener("htmx:afterSwap", initDrop);
})();
// Target selector: picking a vendor ticks that vendor's platforms and unticks the other vendor's
document.addEventListener("change", function (e) {
  var r = e.target.closest("[data-vendor-pick]"); if (!r) return;
  var v = r.getAttribute("data-vendor-pick"), box = r.closest(".target");
  Array.prototype.forEach.call(box.querySelectorAll("input[name=plat]"), function (cb) {
    cb.checked = (v === "both") || cb.getAttribute("data-vendor") === v;
  });
  Array.prototype.forEach.call(box.querySelectorAll("input[name=cpu]"), function (cb) {
    if (v !== "both" && cb.getAttribute("data-vendor") !== v) cb.checked = false;
  });
});
