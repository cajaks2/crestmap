"""Optional map markers for published Malibu and Los Angeles speed camera sites."""

import json


def speed_camera_script(api_path="/api/v1/speed-cameras"):
    return r"""
    function setupSpeedCameraLayer() {
      const toggle = document.querySelector("[data-speed-camera-layer-toggle]");
      if (!toggle) return;
      const layer = L.layerGroup().addTo(map);
      const pane = map.createPane("speedCameraSites");
      pane.style.zIndex = "535";
      const cameraEndpoint = __CAMERA_ENDPOINT__;
      const laSource = "https://ladot.lacity.gov/speed-safety-system";
      const malibuSource = "https://malibucity.org/1235/Speed-Safety-Cameras";
      // Approximate PCH intersection points; Malibu has not published pole coordinates.
      const malibuSites = [
        ["Decker Road", 34.04148, -118.89442],
        ["Morning View Drive", 34.02020, -118.82789],
        ["Latigo Canyon Road", 34.03008, -118.75466],
        ["Carbon Beach Terrace", 34.03936, -118.66007],
        ["Tuna Canyon Road", 34.03947, -118.58941],
      ];
      let visible = window.localStorage.getItem("crestmap-speed-camera-layer") !== "hidden";
      let laSites = [];
      let laLocationCount = 0;
      let laStale = false;
      let fetchedAt = 0;
      let loading = false;
      const markers = new Map();
      function cameraGraphic(state) {
        return `<span class="speed-camera-site is-${state}" aria-hidden="true">` +
          `<svg viewBox="0 0 24 24" focusable="false">` +
          `<path class="speed-camera-warning" d="M12 2 22 20H2Z"/>` +
          `<path class="speed-camera-glyph" d="M7 11.5h2l1-1.3h4l1 1.3h2V17H7Z"/>` +
          `<circle class="speed-camera-lens" cx="12" cy="14.2" r="1.6"/></svg></span>`;
      }
      const iconForStatus = Object.fromEntries(["live", "planned"].map((state) => [state, L.divIcon({
        className: "",
        html: cameraGraphic(state),
        iconSize: [20, 20], iconAnchor: [10, 10], popupAnchor: [0, -12],
      })]));

      function addSite(name, latitude, longitude, status, source, approximate, direction = "", side = "") {
        const live = status === "Warning Live" || status === "Enforcement Live";
        const icon = iconForStatus[live ? "live" : "planned"];
        const description = approximate
          ? "Approximate intersection; exact camera position is unpublished."
          : "LADOT planned mounting point; installed position may differ.";
        const popup = `<strong>${escapeHtml(name)}</strong><br>Speed camera site · ${escapeHtml(status)}` +
          (direction ? `<br>Enforces ${escapeHtml(direction)} traffic` : "") +
          (side ? `<br>Mounted on ${escapeHtml(side)} side` : "") +
          `<br>${description}<br><a href="${source}" target="_blank" rel="noopener">Official source</a>`;
        return L.marker([latitude, longitude], { pane: "speedCameraSites", icon, title: `${name} · ${status}` })
          .bindPopup(popup, { className: "speed-camera-popup", maxWidth: 250 }).addTo(layer);
      }

      function addCluster(group) {
        const center = [group.latitude / group.count, group.longitude / group.count];
        const icon = L.divIcon({
          className: "",
          html: `<span class="speed-camera-cluster" aria-hidden="true">${cameraGraphic("planned")}` +
            `<span class="speed-camera-count">${group.count}</span></span>`,
          iconSize: [30, 24], iconAnchor: [15, 12],
        });
        return L.marker(center, { pane: "speedCameraSites", icon,
          title: `${group.count} LA speed camera ${group.count === 1 ? "location" : "locations"} · zoom in for camera positions` })
          .on("click", () => map.setView(center, Math.max(12, map.getZoom() + 1)))
          .addTo(layer);
      }

      function render() {
        if (!visible) {
          layer.clearLayers();
          markers.clear();
          return;
        }
        const nearby = map.getBounds().pad(0.25);
        const wanted = new Set();
        const visibleLaSites = laSites.map((site, index) => [site, index])
          .filter(([site]) => nearby.contains([site.latitude, site.longitude]));
        const clustered = map.getZoom() < 12 || visibleLaSites.length > 80;
        const clusterCell = 72 * 2 ** Math.max(0, map.getZoom() - 12);
        const groups = new Map();
        if (currentRegion === "malibu") {
          malibuSites.forEach(([name, lat, lon], index) => {
            if (!nearby.contains([lat, lon])) return;
            const key = `malibu-${index}`;
            wanted.add(key);
            if (!markers.has(key)) markers.set(key,
              addSite(`PCH at ${name}`, lat, lon, "Testing / planned", malibuSource, true));
          });
        }
        if (clustered) {
          const locations = new Map();
          visibleLaSites.forEach(([site, index]) => {
            const pairId = site.pair_id || `camera-${index}`;
            if (!locations.has(pairId)) locations.set(pairId,
              { latitude: 0, longitude: 0, poles: 0, indices: [] });
            const location = locations.get(pairId);
            location.latitude += site.latitude;
            location.longitude += site.longitude;
            location.poles++;
            location.indices.push(index);
          });
          locations.forEach((location) => {
            const latitude = location.latitude / location.poles;
            const longitude = location.longitude / location.poles;
            const point = map.project([latitude, longitude], map.getZoom());
            const key = `cluster-${map.getZoom()}-${Math.floor(point.x / clusterCell)}-${Math.floor(point.y / clusterCell)}`;
            if (!groups.has(key)) groups.set(key,
              { count: 0, latitude: 0, longitude: 0, indices: [] });
            const group = groups.get(key);
            group.count++;
            group.latitude += latitude;
            group.longitude += longitude;
            group.indices.push(...location.indices);
          });
        } else {
          visibleLaSites.forEach(([site, index]) => {
            const key = `la-${index}`;
            wanted.add(key);
            if (!markers.has(key)) markers.set(key,
              addSite(site.name, site.latitude, site.longitude, site.status, laSource, false,
                site.direction, site.side));
          });
        }
        groups.forEach((group, key) => {
          const markerKey = `${key}-${group.indices.join("-")}`;
          wanted.add(markerKey);
          if (!markers.has(markerKey)) markers.set(markerKey, addCluster(group));
        });
        markers.forEach((marker, key) => {
          if (wanted.has(key)) return;
          layer.removeLayer(marker);
          markers.delete(key);
        });
      }

      function updateToggle() {
        toggle.classList.toggle("is-active", visible);
        toggle.setAttribute("aria-pressed", String(visible));
        toggle.setAttribute("aria-label", `${visible ? "Hide" : "Show"} speed camera sites`);
        const description = toggle.querySelector(".view-menu-description");
        if (description) description.textContent = !visible ? "Hidden" :
          (currentRegion === "malibu"
            ? `${malibuSites.length} Malibu sites · ${laLocationCount} LA locations${laStale ? " · stale" : ""}`
            : `${laLocationCount} LA locations · ${laSites.length} camera mounts${laStale ? " · stale" : ""}`);
      }

      async function refresh() {
        if (!visible || loading || Date.now() - fetchedAt < 5 * 60 * 1000) return;
        loading = true;
        try {
          const response = await fetch(cameraEndpoint);
          if (!response.ok) throw new Error("LADOT data unavailable");
          const payload = await response.json();
          if (!Array.isArray(payload.sites)) {
            throw new Error("Invalid LADOT data");
          }
          laSites = payload.sites;
          laLocationCount = Number(payload.location_count) || 0;
          laStale = Boolean(payload.stale);
          fetchedAt = Date.now();
          layer.clearLayers();
          markers.clear();
          render();
          updateToggle();
        } catch (_error) {
          const description = toggle.querySelector(".view-menu-description");
          if (description && visible) description.textContent = currentRegion === "malibu"
            ? `${malibuSites.length} Malibu · LA unavailable` : "LA unavailable";
        } finally {
          loading = false;
        }
      }

      L.DomEvent.disableClickPropagation(toggle);
      toggle.addEventListener("click", () => {
        visible = !visible;
        window.localStorage.setItem("crestmap-speed-camera-layer", visible ? "shown" : "hidden");
        render();
        updateToggle();
        if (visible) refresh();
      });
      window.addEventListener("focus", refresh);
      document.addEventListener("visibilitychange", () => {
        if (document.visibilityState === "visible") refresh();
      });
      window.setInterval(refresh, 5 * 60 * 1000);
      map.on("moveend", render);
      render();
      updateToggle();
      refresh();
    }
    setupSpeedCameraLayer();
    """.replace("__CAMERA_ENDPOINT__", json.dumps(api_path))
