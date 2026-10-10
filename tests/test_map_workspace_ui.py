"""Behavioral checks for map label geometry and generated browser code."""

import json
import re
import shutil
import subprocess

import pytest

from generate_live_map import build_html
from temperature_ui import TEMPERATURE_JS


def run_js(source):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required for map client behavior tests")
    result = subprocess.run([node], input=source, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_temperature_labels_stay_close_to_coordinate_and_declutter_overlaps():
    geometry = TEMPERATURE_JS.split("// TEMPERATURE_PLACEMENT_START:", 1)[1]
    geometry = geometry[geometry.index("function boxesOverlap"):].split("// TEMPERATURE_PLACEMENT_END")[0]
    run_js(geometry + r"""
      const assert = require('node:assert/strict');
      const size = {x: 390, y: 560}, pixel = {x: 160, y: 220};
      const first = placeTemperatureLabel(pixel, size, [], 42, 26);
      assert.ok(first);
      assert.equal(first.dx, 6);
      assert.equal(first.dy, -31);
      const alternate = placeTemperatureLabel(pixel, size, [first.box], 42, 26, first.index);
      assert.ok(alternate);
      assert.ok(!boxesOverlap(first.box, alternate.box));
      const incident = {left: 140, right: 180, top: 200, bottom: 240};
      assert.equal(placeTemperatureLabel(pixel, size, [incident], 42, 26), null);
      assert.equal(placeTemperatureLabel(pixel,size,[{left:0,right:390,top:0,bottom:560}],42,26),null);
      const placed = [{pixel:{x:100,y:100},degrees:74}];
      assert.equal(shouldSkipTemperature({x:150,y:100},75,placed,12),true,
        'enforce minimum spacing for different nearby values');
      assert.equal(shouldSkipTemperature({x:210,y:100},74,placed,12),true,
        'suppress the same rounded value across the wider duplicate radius');
      assert.equal(shouldSkipTemperature({x:240,y:100},74,placed,12),false);
    """)


def test_temperature_retry_respects_server_backoff_and_recovers():
    script = TEMPERATURE_JS.replace("__TEMPERATURE_ENDPOINT__", json.dumps("/api/v1/temperature"))
    harness = r"""
      const assert = require('node:assert/strict');
      const vm = require('node:vm');
      const timers = new Map(), intervals = new Map();
      let nextTimer = 1, requests = 0, retryClick = null;
      const statusText = {textContent: ''};
      const description = {textContent: ''};
      const loadStatus = {
        classList: {toggle() {}}, setAttribute() {},
        addEventListener(type, fn) {if (type === 'click') retryClick = fn;},
        querySelector() {return statusText;}, disabled: true
      };
      const button = {
        classList: {toggle() {}}, setAttribute() {}, addEventListener() {},
        querySelector() {return description;}
      };
      const context = {
        L: {layerGroup: () => ({addTo() {return this;}, clearLayers() {}}),
          DomEvent: {disableClickPropagation() {}}},
        map: {createPane: () => ({style: {}}), getContainer: () => ({
          appendChild() {}, getBoundingClientRect() {return {left: 0, top: 0};},
          querySelectorAll() {return [];}
        }), on() {}},
        markers: new Map(), cameraMarkers: new Map(), aircraftMarkers: new Map(),
        currentRegion: 'forest', document: {hidden: false, createElement: () => loadStatus,
          querySelector: () => button, addEventListener() {}},
        navigator: {onLine: true}, localStorage: {getItem: () => null},
        window: {chpLiveMap: {}, addEventListener() {},
          setTimeout(fn, delay) {const id = nextTimer++; timers.set(id, {fn, delay}); return id;},
          clearTimeout(id) {timers.delete(id);},
          setInterval(fn, delay) {const id = nextTimer++; intervals.set(id, {fn, delay}); return id;},
          clearInterval(id) {intervals.delete(id);}},
        fetch: async () => {
          requests++;
          if (requests === 1) return {ok: false, status: 503, headers: {get: () => '2'}};
          if (requests === 3) throw new Error('network lost');
          return {ok: true, json: async () => ({region: 'forest', points: [{kind: 'estimate',
            temperature_f: 70, elevation_m: 1000, latitude: 34.3, longitude: -118.1,
            valid_at: new Date().toISOString()}]})};
        },
        AbortController, Date, Number, Math, Map, Array, String, JSON,
      };
    """
    run_js(harness + "vm.runInNewContext(" + json.dumps(script) + """, context);
      (async () => {
        await new Promise(setImmediate);
        assert.equal(requests, 1);
        assert.equal(loadStatus.disabled, true);
        assert.match(statusText.textContent, /Retrying in [12]s/);
        const retry = [...timers.values()].find(timer => timer.delay === 2000);
        assert.ok(retry, 'server backoff schedules a new request');
        retry.fn();
        await new Promise(setImmediate);
        assert.equal(requests, 2);
        assert.equal(description.textContent, 'Roads + terrain highs/lows · more detail as you zoom');
        const periodic = [...intervals.values()].find(timer => timer.delay === 15 * 60 * 1000);
        periodic.fn();
        await new Promise(setImmediate);
        assert.equal(requests, 3);
        assert.equal(loadStatus.disabled, false);
        assert.equal(statusText.textContent, 'Temperatures unavailable · Tap to retry');
        retryClick();
        await new Promise(setImmediate);
        assert.equal(requests, 4);
        assert.equal(description.textContent, 'Roads + terrain highs/lows · more detail as you zoom');
      })().catch(error => {console.error(error); process.exitCode = 1;});
    """)


@pytest.mark.parametrize("region", ["forest", "malibu"])
def test_rendered_scripts_parse_and_sheet_preserves_full_record(region):
    html = build_html([], "2026-09-10T12:00:00-07:00", 72, region=region)
    scripts = [body for attrs, body in re.findall(r"<script([^>]*)>(.*?)</script>", html, re.S)
               if "application/ld+json" not in attrs]
    run_js("const vm=require('node:vm'); for(const source of " + json.dumps(scripts)
           + ") new vm.Script(source);")
    assert 'id="detail-content"' in html
    assert 'id="map-sheet-preview"' in html
    assert 'id="map-sheet-close" aria-label="Close details"' in html
    assert '#incident-list-handle, #incident-list-close { display: none; }' in html
    assert '#incident-list-handle { display: block;' in html
    assert '#incident-list-close, #map-sheet-close { display: inline-flex;' in html
    assert 'font: 300 30px/1 -apple-system' in html
    assert 'if (event.target.closest("button")) return;' in html
    assert '#incident-list-close:active, #map-sheet-close:active' in html
    assert "function guardMapDuringPaneTransition()" in html
    assert 'guardMapDuringPaneTransition();' in html
    assert '}, 260);' in html
    assert 'new CustomEvent("crestmap:detailclose"' in html
    assert 'map.on("click", dismissPaneFromMap)' in html
    assert 'if (!mobileViewport.matches) return;' in html
    assert 'if (shell.dataset.mapSheet !== "closed")' in html
    assert 'function suspendMapGestures()' in html
    assert 'if (paneGestureMapState.dragging) map.dragging.disable();' in html
    assert 'if (previous.touchZoom) map.touchZoom.enable();' in html
    assert 'Continue a downward content scroll as a sheet drag once the record reaches its top.' in html
    assert 'detailContent.scrollTop > 1' in html
    assert 'detailContent.addEventListener("touchmove"' in html
    assert 'flex: 0 0 44px' in html
    assert '#map-sheet-back { min-height: 40px; margin: 0;' in html
    assert 'id="mobile-connection-status" data-state="online"' in html
    assert 'z-index: 700; font-size: 12px; transition: opacity 160ms ease' in html
    assert 'class="map-activity-dot"' in html
    assert '#scroll-incidents, #scroll-incidents-top { z-index: 4; width: 44px; height: 36px; }' in html
    assert 'start.state === "expanded" ? 0 : -90' in html
    assert 'height: calc(100% - var(--map-header-height, 170px))' in html
    assert 'shell.getBoundingClientRect().height - headerHeight' in html
    assert 'if (target !== "closed") requestAnimationFrame' in html
    assert 'if (target === "closed") setTimeout(() => listShell.style.removeProperty' in html
    assert 'setTimeout(() => {' in html and 'setList(true);' in html
    assert 'id="map-sheet-toggle"' not in html
    assert '(event.target.closest("button") || surface).setPointerCapture' not in html
    assert "suppressClickUntil" in html
    assert "incomingLink = null" in html
    assert "map.panBy([p.x - x, p.y - y], {animate: true, duration: .28" in html
    assert "setTimeout(() => revealPoint(selection), 240)" in html
    assert "Math.max(map.getZoom(), 13)" in html  # desktop behavior is retained
    assert 'marker && options.pan !== false && !mobileViewport.matches' in html
    assert 'data-comment-form' in html
    assert 'data-share-incident' in html


def test_incident_pane_formats_full_reported_date():
    html = build_html([], "2026-10-05T08:15:00-07:00", 72)
    formatter = html.split("function formatIncidentWhenLong(incident) {", 1)[1]
    formatter = "function formatIncidentWhenLong(incident) {" + formatter.split("function incidentSourceLabel", 1)[0]
    run_js(formatter + """
      const assert = require('node:assert/strict');
      assert.equal(formatIncidentWhenLong({incident_date: '2026-10-05', incident_time: '8:15 AM'}),
        'October 5, 2026 at 8:15 AM');
      assert.equal(formatIncidentWhenLong({incident_date: '2026-10-05'}), 'October 5, 2026');
    """)
