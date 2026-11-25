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
// Target selector: external-storage options only for the disaggregated architecture; Nutanix is HCI only
function syncArch(box) {
  if (!box) return;
  var arch = box.querySelector("input[name=arch]:checked"), opts = box.querySelector("[data-external-opts]");
  var stack = box.querySelector("input[name=stack]:checked"), nutanix = stack && stack.value === "nutanix_esxi";  // external storage: VMware or Nutanix AHV
  var ext = box.querySelector("input[name=arch][value=external]");
  if (ext) { ext.disabled = nutanix; if (nutanix && ext.checked) box.querySelector("input[name=arch][value=hci]").checked = true; }
  arch = box.querySelector("input[name=arch]:checked");
  if (opts) opts.style.display = (arch && arch.value === "external") ? "" : "none";
  // Nutanix AHV with PowerStore: NVMe/TCP only (NCI 7.6) - select it and grey out the other protocols
  var ahv = stack && stack.value === "nutanix_ahv", proto = box.querySelector("select[name=es_protocol]");
  if (proto) {
    Array.prototype.forEach.call(proto.options, function (o) { o.disabled = ahv && o.value !== "nvme_tcp25"; });
    if (ahv) { proto.value = "nvme_tcp25"; }
  }
}
document.addEventListener("change", function (e) {
  if (e.target.name === "arch" || e.target.name === "stack") syncArch(e.target.closest(".target"));
});
function initArch() { Array.prototype.forEach.call(document.querySelectorAll(".target"), syncArch); }
document.addEventListener("DOMContentLoaded", initArch);
document.addEventListener("htmx:afterSwap", initArch);
// Email prompt: copy to clipboard (falls back to select + execCommand when the Clipboard API is unavailable)
document.addEventListener("click", function (e) {
  var b = e.target.closest("#copy-prompt"); if (!b) return;
  var ta = document.getElementById("prompt"), msg = document.getElementById("copy-msg");
  function done() { if (msg) msg.textContent = b.getAttribute("data-done"); }
  if (navigator.clipboard && window.isSecureContext) { navigator.clipboard.writeText(ta.value).then(done); }
  else { ta.select(); document.execCommand("copy"); done(); }
});

// Reports list: select all, selection count, buttons enabled only with a selection, confirm before delete
(function () {
  function sync() {
    var form = document.getElementById("repsel"); if (!form) return;
    var boxes = form.querySelectorAll("input[name=f]"), n = 0;
    Array.prototype.forEach.call(boxes, function (b) { if (b.checked) n++; });
    Array.prototype.forEach.call(form.querySelectorAll("[data-needs-selection]"), function (b) { b.disabled = n === 0; });
    var all = document.getElementById("repsel-all");
    if (all) { all.checked = n > 0 && n === boxes.length; all.indeterminate = n > 0 && n < boxes.length; }
    var c = document.getElementById("repsel-count"); if (c) c.textContent = n ? n + " / " + boxes.length : "";
  }
  document.addEventListener("change", function (e) {
    if (e.target.id === "repsel-all") {
      Array.prototype.forEach.call(document.querySelectorAll("#repsel input[name=f]"), function (b) { b.checked = e.target.checked; });
    }
    if (e.target.id === "repsel-all" || e.target.name === "f") sync();
  });
  document.addEventListener("click", function (e) {
    var b = e.target.closest("#repsel [data-confirm]");
    if (b && !window.confirm(b.getAttribute("data-confirm"))) e.preventDefault();
  });
  document.addEventListener("DOMContentLoaded", sync);
})();
