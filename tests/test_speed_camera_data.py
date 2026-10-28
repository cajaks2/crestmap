import json
import shutil
import subprocess

import pytest

from fastapi.testclient import TestClient

import app
import speed_camera_data as cameras
from app import WebSettings, create_app
from scrape_chp_traffic import connect_database
from serve_live_map import prometheus_metrics
from speed_camera_ui import speed_camera_script


class FakeResponse:
    def __init__(self, payload):
        self.body = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit):
        return self.body


def feature(lon, lat, **properties):
    return {"geometry": {"coordinates": [lon, lat]}, "properties": properties}


def test_server_cache_limits_arcgis_requests_and_preserves_stale_data(monkeypatch):
    cameras.reset_for_tests()
    now = [1000.0]
    calls = []
    layers = {
        "systems": {"features": [feature(-118.5, 34.0, System="Warning Live")]},
        "poles": {"features": [
            feature(-118.5001, 34.0, PairID="1", Primary_Street="Main St", Traffic_Direction_Enforced="Eastbound"),
            feature(-118.4999, 34.0, PairID="1", Primary_Street="Main St"),
        ]},
    }

    def fake_urlopen(request, timeout):
        calls.append(request.full_url)
        layer = "systems" if "CameraSystemLabels" in request.full_url else "poles"
        return FakeResponse(layers[layer])

    monkeypatch.setattr(cameras.time, "time", lambda: now[0])
    monkeypatch.setattr(cameras, "urlopen", fake_urlopen)
    first = cameras.load_speed_cameras()
    assert len(first["sites"]) == 2
    assert first["location_count"] == 1
    assert first["sites"][0]["pair_id"] == "1"
    assert first["sites"][0]["status"] == "Warning Live"
    assert first["stale"] is False
    assert len(calls) == 2
    assert cameras.load_speed_cameras()["stale"] is False
    assert len(calls) == 2

    now[0] += 301

    def unavailable(_request, timeout):
        calls.append("failed")
        raise OSError("upstream unavailable")

    monkeypatch.setattr(cameras, "urlopen", unavailable)
    assert cameras.load_speed_cameras()["stale"] is True
    assert len(calls) == 4
    assert cameras.load_speed_cameras()["stale"] is True
    assert len(calls) == 4
    metrics = cameras.metrics_snapshot()
    assert metrics["refresh_total"] == {"success": 1, "failure": 1}
    assert metrics["cache_total"]["retry_suppressed"] == 1
    assert metrics["site_counts"]["poles"] == 2
    cameras.reset_for_tests()


def test_speed_camera_api_and_metrics(tmp_path, monkeypatch):
    database = tmp_path / "camera.sqlite"
    connect_database(database).close()
    payload = {"sites": [{"pair_id": "1", "name": "Main St", "latitude": 34.0, "longitude": -118.5,
                          "status": "Warning Live", "direction": "", "side": ""}],
               "location_count": 1, "updated_at": 1000, "stale": False}
    monkeypatch.setattr(app, "load_speed_cameras", lambda: payload)
    settings = WebSettings(database=database, base_path="/map")
    with TestClient(create_app(settings)) as client:
        response = client.get("/map/api/v1/speed-cameras")
        assert response.status_code == 200
        assert response.json() == payload
        assert response.headers["Cache-Control"] == "public, max-age=60"
        assert 'const cameraEndpoint = "/map/api/v1/speed-cameras"' in client.get("/map").text
        metrics = client.get("/metrics").text
        assert 'crestmap_http_requests_total{method="GET",route="speed_cameras",status="200"}' in metrics
        assert "crestmap_speed_camera_arcgis_refresh_total" in metrics
        assert "crestmap_speed_camera_sites" in metrics

    def unavailable():
        raise cameras.SpeedCameraUnavailable()

    monkeypatch.setattr(app, "load_speed_cameras", unavailable)
    with TestClient(create_app(settings)) as client:
        response = client.get("/map/api/v1/speed-cameras")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "speed_cameras_unavailable"


def test_camera_metrics_exist_before_first_pull(tmp_path):
    cameras.reset_for_tests()
    metrics = prometheus_metrics(tmp_path / "missing.sqlite", None, 72).decode()
    assert 'crestmap_speed_camera_arcgis_requests_total{layer="poles",outcome="success"} 0' in metrics
    assert 'crestmap_speed_camera_sites{kind="poles"} 0' in metrics


def test_camera_markers_only_exist_near_the_map_view():
    if not shutil.which("node"):
        pytest.skip("Node is required for the camera marker runtime test")
    script = f"""
const vm = require('node:vm');
const assert = require('node:assert/strict');
const cameraScript = {json.dumps(speed_camera_script())};
let bounds = [34.5, 34.7, -118.2, -118.0];
let zoom = 10;
let created = 0;
let removed = 0;
const markerTitles = [];
let clusterClick;
let clusterTitle = '';
const listeners = {{}};
const layer = {{ addTo() {{ return this; }}, clearLayers() {{}}, removeLayer() {{ removed++; }} }};
const map = {{
  createPane() {{ return {{ style: {{}} }}; }},
  getBounds() {{ return {{ pad() {{ return {{ contains([lat, lon]) {{
    return bounds[0] <= lat && lat <= bounds[1] && bounds[2] <= lon && lon <= bounds[3];
  }} }}; }} }}; }},
  getZoom() {{ return zoom; }},
  project([lat, lon]) {{ return {{ x: lon * 100, y: lat * 100 }}; }},
  setView(_center, newZoom) {{ zoom = newZoom; listeners.moveend(); }},
  on(name, callback) {{ listeners[name] = callback; }},
}};
const toggle = {{ classList: {{ toggle() {{}} }}, setAttribute() {{}},
  querySelector() {{ return {{ textContent: '' }}; }}, addEventListener() {{}} }};
const sites = Array.from({{ length: 246 }}, (_, index) => ({{
  name: 'Camera ' + index, status: 'Under Construction',
  pair_id: index < 2 ? 'paired-location' : String(index),
  latitude: index < 2 ? 34.3 : 33.7,
  longitude: index < 2 ? -118.1 : -118.5,
}}));
const context = {{ map, currentRegion: 'forest', escapeHtml: (value) => value,
  document: {{ querySelector() {{ return toggle; }}, addEventListener() {{}} }},
  window: {{ localStorage: {{ getItem() {{ return null; }}, setItem() {{}} }},
    addEventListener() {{}}, setInterval() {{}} }},
  fetch: async () => ({{ ok: true, json: async () => ({{ sites, location_count: 245, stale: false }}) }}),
  L: {{ layerGroup: () => layer, divIcon: (options) => options,
    marker: (_point, options) => {{ created++; markerTitles.push(options.title);
      if (options.title.includes('zoom in')) clusterTitle = options.title;
      return {{ bindPopup() {{ return this; }},
      on(_event, callback) {{ clusterClick = callback; return this; }},
      addTo() {{ return this; }} }}; }},
    DomEvent: {{ disableClickPropagation() {{}} }},
  }},
}};
vm.runInNewContext(cameraScript, context);
setImmediate(() => {{
  assert.equal(created, 0);
  bounds = [34.2, 34.4, -118.2, -118.0];
  listeners.moveend();
  assert.equal(created, 1);
  assert.ok(clusterTitle.startsWith('1 LA speed camera location'));
  listeners.moveend();
  assert.equal(created, 1);
  clusterClick();
  assert.equal(zoom, 12);
  assert.equal(created, 3);
  assert.equal(removed, 1);
  bounds = [33.5, 34.4, -118.7, -118.0];
  listeners.moveend();
  assert.equal(markerTitles.filter((title) => title.startsWith('Glendale:')).length, 9);
  assert.equal(markerTitles.filter((title) => title.startsWith('Long Beach:')).length, 18);
  assert.equal(markerTitles.filter((title) => title.startsWith('Malibu:')).length, 2);
  assert.ok(created < 40, 'wide views keep LA mounting points grouped');
  bounds = [34.5, 34.7, -118.2, -118.0];
  listeners.moveend();
  assert.ok(removed >= 3);
}});
"""
    subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
