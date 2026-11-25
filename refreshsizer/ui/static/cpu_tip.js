// CPU details popup (cores, GHz, cache, TDP) - generated from refreshsizer/catalog/cpu_info.py
(function () {
  var tip = null;
  function cpuOf(t) { while (t && t.nodeType !== 1) { t = t.parentNode; }
    return (t && t.closest) ? t.closest(".cpu[data-tip]") : null; }
  function show(el) {
    if (el.hasAttribute("title")) { el.removeAttribute("title"); }
    if (!tip) { tip = document.createElement("div"); tip.id = "cpu-tip"; document.body.appendChild(tip); }
    tip.textContent = el.getAttribute("data-tip");
    tip.style.display = "block";
    var r = el.getBoundingClientRect(), w = tip.offsetWidth, h = tip.offsetHeight;
    var x = Math.min(r.left, window.innerWidth - w - 8), y = r.bottom + 6;
    if (y + h > window.innerHeight - 4) { y = r.top - h - 6; }
    tip.style.left = Math.max(4, x) + "px"; tip.style.top = Math.max(4, y) + "px";
  }
  function hide() { if (tip) { tip.style.display = "none"; } }
  document.addEventListener("mouseover", function (e) { var el = cpuOf(e.target); if (el) { show(el); } else { hide(); } });
  document.addEventListener("focusin", function (e) { var el = cpuOf(e.target); if (el) { show(el); } });
  document.addEventListener("focusout", hide);
  document.addEventListener("mouseleave", hide);
  window.addEventListener("scroll", hide, true);
})();
