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
    assert 'flex: 0 0 52px' in html
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
