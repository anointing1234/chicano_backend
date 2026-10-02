/*
 * Live map for Overview and Dispatch.
 *
 * Markup: <div class="map" data-live-map="/dashboard/live/data/?..." data-every="10000"></div>
 *         <div class="map-panel" data-map-panel hidden></div>   (details of the clicked marker)
 *
 * Data comes from views/live.py (real driver/rider GPS positions and waiting rides), refreshed
 * every 10 s. With Leaflet loaded (from cdnjs) you get real street tiles (Esri World Street Map, no API key);
 * without internet it falls back to a plain schematic plot of the same data, so the page still works.
 */
(function () {
  "use strict";
  var el = document.querySelector("[data-live-map]");
  if (!el) return;
  var panel = document.querySelector("[data-map-panel]");
  var countEl = document.querySelector("[data-map-count]");
  var every = parseInt(el.getAttribute("data-every") || "10000", 10);
  var LAGOS = [6.52, 3.38];
  var PHASE = { available: "#2E9E5B", pickup: "#E7770B", trip: "#16130F" };
  var map = null, layer = null, fitted = false;

  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }

  function showPanel(html) {
    if (!panel) return;
    panel.innerHTML = '<button type="button" class="dialog-close" style="float:right" aria-label="Close details" data-panel-close>×</button>' + html;
    panel.hidden = false;
  }
  if (panel) panel.addEventListener("click", function (e) { if (e.target.closest("[data-panel-close]")) panel.hidden = true; });

  function driverHtml(d) {
    return '<h3>' + esc(d.name) + '</h3><p class="small muted">' + esc(d.kind) + ' · ' + esc(d.vehicle || "no vehicle") + '</p>' +
      '<p class="small">' + (d.phase === "pickup" ? '<span class="badge badge-warn">To pickup</span>' : d.on_trip ? '<span class="badge badge-dark">On trip</span>' : '<span class="badge badge-good">Available</span>') +
      ' <span class="muted">GPS ' + esc(d.updated) + '</span></p><a class="btn btn-secondary btn-sm" href="' + esc(d.url) + '">Open profile</a>';
  }
  function rideHtml(r) {
    return '<h3>Waiting ride</h3><p class="small">' + esc(r.pickup) + '<br><span class="muted">→ ' + esc(r.dropoff) + '</span></p>' +
      '<p class="small">' + (r.manual ? '<span class="badge badge-bad">Needs manual dispatch</span>' : '<span class="badge badge-warn">Searching</span>') +
      ' <span class="muted">' + esc(r.waiting) + '</span></p>' +
      '<a class="btn btn-primary btn-sm" href="' + esc(r.assign_url) + '">Assign</a> <a class="btn btn-ghost btn-sm" href="' + esc(r.url) + '">Trip</a>';
  }

  // ---------------------------------------------------------------- street tiles
  // Free sources that need no API key or account:
  //   1. Esri World Street Map (main)   2. Esri World Topographic (backup if the first fails to load)
  // Not used: tile.openstreetmap.org ("403 Access blocked" for apps) and CARTO (now needs an API key and
  // returns an "API KEY REQUIRED" picture instead of an error, so it can't be detected and skipped).
  // To use a provider with your own key later (e.g. MapTiler), add it as the first entry below.
  var ESRI_ATTR = 'Tiles &copy; <a href="https://www.esri.com">Esri</a> &mdash; Esri, HERE, Garmin, FAO, NOAA, USGS, &copy; OpenStreetMap contributors';
  var TILE_SOURCES = [
    { url: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}",
      options: { maxZoom: 19, attribution: ESRI_ATTR } },
    { url: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}",
      options: { maxZoom: 19, attribution: ESRI_ATTR } }
  ];
  function addTiles(m, index) {
    index = index || 0;
    var src = TILE_SOURCES[index];
    if (!src) return;                                         // every source failed: markers still show on the grey map
    var opts = { referrerPolicy: "strict-origin-when-cross-origin", crossOrigin: true };
    for (var k in src.options) opts[k] = src.options[k];
    var layer = L.tileLayer(src.url, opts), errors = 0, loaded = 0, switched = false;
    layer.on("tileload", function () { loaded += 1; });
    layer.on("tileerror", function () {
      errors += 1;
      if (!switched && errors >= 4 && loaded === 0) {        // this source is down or blocked: try the next one once
        switched = true;
        m.removeLayer(layer);
        addTiles(m, index + 1);
      }
    });
    layer.addTo(m);
  }

  // ---------------------------------------------------------------- Leaflet renderer
  function drawLeaflet(data) {
    if (!map) {
      el.innerHTML = "";
      map = L.map(el, { zoomControl: !el.hasAttribute("data-no-zoom"), attributionControl: true, scrollWheelZoom: el.hasAttribute("data-scroll-zoom") }).setView(LAGOS, 11);
      addTiles(map);
      layer = L.layerGroup().addTo(map);
      new ResizeObserver(function () { map.invalidateSize(); }).observe(el);
    }
    layer.clearLayers();
    var bounds = [];
    data.drivers.forEach(function (d) {
      // Board markers: a small vehicle pill (cars wide, bikes tall), green available · orange to pickup · ink on trip.
      var color = PHASE[d.phase || (d.on_trip ? "trip" : "available")];
      var bike = d.service === "bike";
      L.marker([d.lat, d.lng], { icon: L.divIcon({ className: "", iconSize: bike ? [12, 20] : [22, 12], iconAnchor: bike ? [6, 10] : [11, 6],
          html: '<span class="vmark' + (bike ? " bike" : "") + '" style="background:' + color + '"></span>' }) })
        .bindTooltip(esc(d.name)).on("click", function () { showPanel(driverHtml(d)); }).addTo(layer);
      bounds.push([d.lat, d.lng]);
    });
    data.rides.forEach(function (r) {
      L.marker([r.lat, r.lng], { icon: L.divIcon({ className: "", html: '<span class="pin pin-ride" style="position:static;display:block;transform:none"></span>', iconSize: [18, 18], iconAnchor: [9, 9] }) })
        .bindTooltip("Waiting: " + esc(r.pickup)).on("click", function () { showPanel(rideHtml(r)); }).addTo(layer);
      bounds.push([r.lat, r.lng]);
    });
    if (!fitted && bounds.length) { map.fitBounds(bounds, { padding: [30, 30], maxZoom: 14 }); fitted = true; }
  }

  // ---------------------------------------------------------------- offline fallback renderer
  function drawFallback(data) {
    var pts = data.drivers.map(function (d) { return [d.lat, d.lng]; }).concat(data.rides.map(function (r) { return [r.lat, r.lng]; }));
    var minLat = 6.38, maxLat = 6.70, minLng = 3.10, maxLng = 3.70;
    if (pts.length) {
      minLat = Math.min.apply(null, pts.map(function (p) { return p[0]; })) - 0.02; maxLat = Math.max.apply(null, pts.map(function (p) { return p[0]; })) + 0.02;
      minLng = Math.min.apply(null, pts.map(function (p) { return p[1]; })) - 0.02; maxLng = Math.max.apply(null, pts.map(function (p) { return p[1]; })) + 0.02;
    }
    function pos(lat, lng) { return "left:" + ((lng - minLng) / (maxLng - minLng) * 100).toFixed(2) + "%;top:" + ((1 - (lat - minLat) / (maxLat - minLat)) * 100).toFixed(2) + "%"; }
    var html = '<div class="map-fallback" role="img" aria-label="Positions of online drivers and riders (street map unavailable offline)">';
    data.drivers.forEach(function (d, i) {
      var cls = (d.phase === "trip" || (!d.phase && d.on_trip)) ? "pin-busy" : (d.phase === "pickup" ? "pin-pickup" : "pin-car");
      html += '<button type="button" class="pin ' + cls + (d.service === "bike" ? " pin-bike" : "") + '" style="' + pos(d.lat, d.lng) + '" data-d="' + i + '" aria-label="' + esc(d.name) + '"></button>';
    });
    data.rides.forEach(function (r, i) {
      html += '<button type="button" class="pin pin-ride" style="' + pos(r.lat, r.lng) + '" data-r="' + i + '" aria-label="Waiting ride at ' + esc(r.pickup) + '"></button>';
    });
    el.innerHTML = html + "</div>";
    el.onclick = function (e) {
      var b = e.target.closest(".pin"); if (!b) return;
      if (b.dataset.d) showPanel(driverHtml(data.drivers[+b.dataset.d]));
      if (b.dataset.r) showPanel(rideHtml(data.rides[+b.dataset.r]));
    };
  }

  function load() {
    if (document.hidden) return;
    fetch(el.getAttribute("data-live-map"), { credentials: "same-origin", headers: { Accept: "application/json" } })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (data) {
        el.classList.remove("skeleton");
        if (countEl) countEl.textContent = data.drivers.length + " online · " + data.rides.length + " waiting";
        if (window.L && window.ResizeObserver) drawLeaflet(data); else drawFallback(data);
      })
      .catch(function () {
        el.classList.remove("skeleton");
        if (!map) el.innerHTML = '<div class="empty"><div class="empty-title">Unable to load the live map</div><p>We\'ll try again in a few seconds.</p></div>';
      });
  }
  load();
  setInterval(load, every);
  el.addEventListener("refresh", function () { fitted = false; load(); });   // filters changed
})();
