"""Optional map markers for published Malibu, Glendale, and Los Angeles speed camera sites."""

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
      const glendaleSource = "https://www.glendaleca.gov/home/showpublisheddocument/78486/638816254653370000";
      const longBeachSource = "https://longbeach.gov/globalassets/pw/media-library/documents/projects/automated-speed-enforcement/speed-system-impact-report";
      // Approximate PCH intersection points; Malibu has not published pole coordinates.
      const malibuSites = [
        ["Decker Road", 34.04148, -118.89442],
        ["Morning View Drive", 34.02020, -118.82789],
        ["Latigo Canyon Road", 34.03008, -118.75466],
        ["Carbon Beach Terrace", 34.03936, -118.66007],
        ["Tuna Canyon Road", 34.03947, -118.58941],
      ];
      // One representative point per corridor in Glendale's impact report. These are
      // address-block estimates, not surveyed pole locations or confirmed active cameras.
      const glendaleSites = [
        ["Glendale Ave (700 block)", 34.156226, -118.241712],
        ["Brand Blvd (800 block)", 34.137091, -118.254816],
        ["Glenoaks Blvd (300 block)", 34.159291, -118.257827],
        ["San Fernando Rd (5300 block)", 34.148918, -118.272827],
        ["Glenoaks Blvd (1200 block)", 34.164983, -118.281489],
        ["Glenoaks Blvd (1800 block)", 34.172459, -118.291760],
        ["Brand Blvd (300 block)", 34.150074, -118.254604],
        ["Central Ave (200 block)", 34.148104, -118.257827],
        ["Chevy Chase Dr (1700 block)", 34.153992, -118.227819],
      ];
      // Approximate midpoints of the 18 published Long Beach road segments. The
      // impact report does not publish individual camera coordinates or directions.
      const longBeachSites = [
        ["Artesia Blvd (Harbor–Butler)", 33.874297, -118.202026],
        ["2nd St (Bay Shore–Appian)", 33.757333, -118.124002],
        ["Pacific Ave (Willow–PCH)", 33.797153, -118.193750],
        ["7th St (Cherry–Termino)", 33.775330, -118.156783],
        ["Atlantic Ave (LA River–Artesia)", 33.859713, -118.185620],
        ["Long Beach Blvd (Victoria–Market)", 33.859585, -118.198319],
        ["Ocean Blvd (Pacific–Atlantic)", 33.766765, -118.189376],
        ["Willow St (Pacific–Atlantic)", 33.804388, -118.189405],
        ["Anaheim St (Redondo–Termino)", 33.782593, -118.149153],
        ["Shoreline Dr (Pine–Ocean)", 33.764559, -118.187917],
        ["Ocean Blvd (Alamitos–Orange)", 33.765958, -118.180238],
        ["7th St (Park–Santiago)", 33.775333, -118.132956],
        ["Bellflower Blvd (Stearns–Willow)", 33.799606, -118.125399],
        ["Cherry Ave (Wardlow–Bixby)", 33.822805, -118.167637],
        ["Anaheim St (Atlantic–Walnut)", 33.782597, -118.178516],
        ["Santa Fe Ave (PCH–21st)", 33.792576, -118.215495],
        ["Long Beach Blvd (San Antonio–45th)", 33.836846, -118.190186],
        ["Long Beach Blvd (Artesia–70th)", 33.877560, -118.203747],
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

      function addSite(name, latitude, longitude, status, source, approximate, direction = "", side = "", detail = "") {
        const live = status === "Warning Live" || status === "Enforcement Live";
        const icon = iconForStatus[live ? "live" : "planned"];
        const description = detail || (approximate
          ? "Approximate intersection; exact camera position is unpublished."
          : "LADOT planned mounting point; installed position may differ.");
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
        malibuSites.forEach(([name, lat, lon], index) => {
          if (!nearby.contains([lat, lon])) return;
          const key = `malibu-${index}`;
          wanted.add(key);
          if (!markers.has(key)) markers.set(key,
            addSite(`Malibu: PCH at ${name}`, lat, lon, "Testing / planned", malibuSource, true));
        });
        glendaleSites.forEach(([name, lat, lon], index) => {
          if (!nearby.contains([lat, lon])) return;
          const key = `glendale-${index}`;
          wanted.add(key);
          if (!markers.has(key)) markers.set(key,
            addSite(`Glendale: ${name}`, lat, lon, "Proposed; activation unconfirmed",
              glendaleSource, true, "", "",
              "Approximate block from Glendale's impact report; exact camera position is unpublished."));
        });
        longBeachSites.forEach(([name, lat, lon], index) => {
          if (!nearby.contains([lat, lon])) return;
          const key = `long-beach-${index}`;
          wanted.add(key);
          if (!markers.has(key)) markers.set(key,
            addSite(`Long Beach: ${name}`, lat, lon, "Published corridor; activation varies",
              longBeachSource, true, "", "",
              "Approximate corridor midpoint; exact camera position and site status are not published."));
        });
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
          `${laLocationCount} LA · ${glendaleSites.length} Glendale · ${longBeachSites.length} Long Beach · ` +
          `${malibuSites.length} Malibu${laStale ? " · LA stale" : ""}`;
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
          if (description && visible) description.textContent =
            `${glendaleSites.length} Glendale · ${longBeachSites.length} Long Beach · ` +
            `${malibuSites.length} Malibu · LA unavailable`;
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
