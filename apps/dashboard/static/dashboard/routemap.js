/*
 * Route map for a trip (A03): pickup → stops → drop-off as a dark line, the driver/rider's live position if on a trip.
 * Markup: <div class="map" data-route-map data-pickup="lat,lng" data-dropoff="lat,lng" data-stops="lat,lng;…" data-driver="lat,lng">
 * Uses Leaflet + Esri street tiles when online; otherwise draws a simple schematic of the same points.
 */
(function () {
  "use strict";
  var el = document.querySelector("[data-route-map]");
  if (!el) return;
  function pt(s) { if (!s) return null; var p = s.split(",").map(Number); return isFinite(p[0]) && isFinite(p[1]) ? p : null; }
  var pickup = pt(el.dataset.pickup), dropoff = pt(el.dataset.dropoff), driver = pt(el.dataset.driver);
  var stops = (el.dataset.stops || "").split(";").map(pt).filter(Boolean);
  var path = [pickup].concat(stops, [dropoff]).filter(Boolean);
  if (!path.length) return;

  function dot(color, square) {
    return L.divIcon({ className: "", iconSize: [18, 18], iconAnchor: [9, 9],
      html: '<span style="display:block;width:18px;height:18px;border-radius:' + (square ? "4px" : "50%") + ';background:' + color + ';border:4px solid #fff;box-shadow:0 1px 4px rgba(22,19,15,.4)"></span>' });
  }
  function leaflet() {
    var map = L.map(el, { zoomControl: false, attributionControl: true, scrollWheelZoom: false });
    L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}",
      { maxZoom: 19, attribution: "Tiles &copy; Esri" }).addTo(map);
    L.polyline(path, { color: "#16130F", weight: 5, opacity: .9 }).addTo(map);
    L.marker(pickup, { icon: dot("#E7770B") }).bindTooltip("Pickup").addTo(map);
    L.marker(dropoff, { icon: dot("#16130F", true) }).bindTooltip("Drop-off").addTo(map);
    stops.forEach(function (s, i) { L.marker(s, { icon: dot("#6B645A") }).bindTooltip("Stop " + (i + 1)).addTo(map); });
    if (driver) L.marker(driver, { icon: dot("#2E9E5B") }).bindTooltip("Live position").addTo(map);
    map.fitBounds(L.latLngBounds(path.concat(driver ? [driver] : [])), { padding: [36, 36], maxZoom: 15 });
  }
  function schematic() {
    var all = path.concat(driver ? [driver] : []);
    var la = all.map(function (p) { return p[0]; }), ln = all.map(function (p) { return p[1]; });
    var minA = Math.min.apply(null, la), maxA = Math.max.apply(null, la), minN = Math.min.apply(null, ln), maxN = Math.max.apply(null, ln);
    var W = 600, H = 300, pad = 40;
    function xy(p) { return [pad + (p[1] - minN) / ((maxN - minN) || 1) * (W - 2 * pad), H - pad - (p[0] - minA) / ((maxA - minA) || 1) * (H - 2 * pad)]; }
    var pts = path.map(xy);
    var svg = '<svg viewBox="0 0 ' + W + ' ' + H + '" width="100%" height="100%" preserveAspectRatio="xMidYMid slice" role="img" aria-label="Route">' +
      '<rect width="100%" height="100%" fill="#EAE4DA"/><polyline points="' + pts.map(function (p) { return p.join(","); }).join(" ") +
      '" fill="none" stroke="#16130F" stroke-width="5" stroke-linejoin="round"/>' +
      '<circle cx="' + pts[0][0] + '" cy="' + pts[0][1] + '" r="8" fill="#E7770B" stroke="#fff" stroke-width="4"/>' +
      '<rect x="' + (pts[pts.length - 1][0] - 8) + '" y="' + (pts[pts.length - 1][1] - 8) + '" width="16" height="16" rx="3" fill="#16130F" stroke="#fff" stroke-width="4"/></svg>';
    el.innerHTML = svg;
  }
  function go() { if (window.L) leaflet(); else schematic(); }
  if (document.readyState === "complete") go(); else window.addEventListener("load", go);
})();
