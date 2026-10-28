"""Shared LADOT speed-camera cache and ArcGIS pull telemetry."""

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import json
import math
import threading
import time
from urllib.request import Request, urlopen


SYSTEM_ENDPOINT = (
    "https://services.arcgis.com/G3nmNsarwQblLhip/arcgis/rest/services/"
    "CameraSystemLabels_view/FeatureServer/0/query?where=1%3D1&"
    "outFields=ID%2CSystem&returnGeometry=true&outSR=4326&f=geojson"
)
POLE_ENDPOINT = (
    "https://services.arcgis.com/G3nmNsarwQblLhip/arcgis/rest/services/"
    "CameraLocations_09302026/FeatureServer/0/query?where=1%3D1&"
    "outFields=PairID%2CPrimary_Street%2CFrom_%2CTo_%2C"
    "Traffic_Direction_Enforced%2CSide_of_Street_Camera_is_Mounted_on&"
    "returnGeometry=true&outSR=4326&f=geojson"
)
CACHE_SECONDS = 5 * 60
RETRY_SECONDS = 60
MAX_STALE_SECONDS = 24 * 60 * 60
MAX_RESPONSE_BYTES = 2_000_000
REQUEST_TIMEOUT_SECONDS = 8

_refresh_lock = threading.Lock()
_state_lock = threading.Lock()
_cache = None
_fetched_at = 0.0
_retry_after = 0.0
_refresh_total = Counter()
_request_total = Counter()
_cache_total = Counter()
_last_duration = 0.0
_last_success = 0.0
_site_counts = {"systems": 0, "pairs": 0, "poles": 0}


class SpeedCameraUnavailable(Exception):
    """No recent LADOT camera data is available."""


def _fetch_layer(layer, url):
    try:
        request = Request(url, headers={"User-Agent": "Crestmap/1.0 (+https://crestmap.us/about)"})
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            raise ValueError("ArcGIS response is too large")
        payload = json.loads(body)
        if not isinstance(payload, dict) or not isinstance(payload.get("features"), list):
            raise ValueError("ArcGIS response has no features")
    except (OSError, ValueError, TypeError):
        with _state_lock:
            _request_total[(layer, "failure")] += 1
        raise
    with _state_lock:
        _request_total[(layer, "success")] += 1
    return payload["features"]


def _point(feature):
    if not isinstance(feature, dict):
        return None
    geometry = feature.get("geometry") or {}
    if not isinstance(geometry, dict):
        return None
    coordinates = geometry.get("coordinates") or []
    if not isinstance(coordinates, (list, tuple)) or len(coordinates) < 2:
        return None
    try:
        longitude, latitude = float(coordinates[0]), float(coordinates[1])
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(latitude) and math.isfinite(longitude)):
        return None
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None
    return latitude, longitude


def _parse_sites(system_features, pole_features):
    systems = []
    for feature in system_features:
        coordinates = _point(feature)
        if coordinates:
            properties = feature.get("properties") or {}
            if not isinstance(properties, dict):
                continue
            systems.append((coordinates, str(properties.get("System") or "Status unknown")))
    groups = {}
    for feature in pole_features:
        coordinates = _point(feature)
        if not coordinates:
            continue
        properties = feature.get("properties") or {}
        if not isinstance(properties, dict):
            continue
        pair_id = str(properties.get("PairID") or "")
        if not pair_id:
            continue
        site = {
            "pair_id": pair_id,
            "name": str(properties.get("Primary_Street") or "LADOT speed camera"),
            "direction": str(properties.get("Traffic_Direction_Enforced") or ""),
            "side": str(properties.get("Side_of_Street_Camera_is_Mounted_on") or ""),
            "latitude": coordinates[0],
            "longitude": coordinates[1],
        }
        groups.setdefault(pair_id, []).append(site)
    if not systems or not groups:
        raise ValueError("ArcGIS camera layers are empty")
    sites = []
    for group in groups.values():
        latitude = sum(site["latitude"] for site in group) / len(group)
        longitude = sum(site["longitude"] for site in group) / len(group)
        nearest = min(
            systems,
            key=lambda system: (system[0][0] - latitude) ** 2 +
            ((system[0][1] - longitude) * math.cos(math.radians(latitude))) ** 2,
        )
        for site in group:
            site["status"] = nearest[1]
            sites.append(site)
    return {"sites": sites, "counts": {
        "systems": len(systems), "pairs": len(groups), "poles": len(sites),
    }}


def _usable_cache(now):
    return _cache is not None and now - _fetched_at <= MAX_STALE_SECONDS


def load_speed_cameras():
    """Return a shared snapshot; at most one process-local ArcGIS refresh runs at once."""
    global _cache, _fetched_at, _retry_after, _last_duration, _last_success, _site_counts
    with _refresh_lock:
        now = time.time()
        with _state_lock:
            if _cache is not None and now - _fetched_at < CACHE_SECONDS:
                _cache_total["hit"] += 1
                return {**_cache, "stale": False}
            if now < _retry_after:
                _cache_total["retry_suppressed"] += 1
                if _usable_cache(now):
                    return {**_cache, "stale": True}
                raise SpeedCameraUnavailable()
            _cache_total["miss"] += 1
        started = time.monotonic()
        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                systems_future = executor.submit(_fetch_layer, "systems", SYSTEM_ENDPOINT)
                poles_future = executor.submit(_fetch_layer, "poles", POLE_ENDPOINT)
                parsed = _parse_sites(systems_future.result(), poles_future.result())
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            with _state_lock:
                _refresh_total["failure"] += 1
                _last_duration = time.monotonic() - started
                _retry_after = time.time() + RETRY_SECONDS
                if _usable_cache(time.time()):
                    _cache_total["stale"] += 1
                    return {**_cache, "stale": True}
            raise SpeedCameraUnavailable() from None
        now = time.time()
        with _state_lock:
            _cache = {"sites": parsed["sites"], "location_count": parsed["counts"]["pairs"],
                      "updated_at": now}
            _fetched_at = now
            _retry_after = 0.0
            _refresh_total["success"] += 1
            _last_duration = time.monotonic() - started
            _last_success = now
            _site_counts = parsed["counts"]
            return {**_cache, "stale": False}


def metrics_snapshot():
    with _state_lock:
        return {
            "refresh_total": dict(_refresh_total),
            "request_total": dict(_request_total),
            "cache_total": dict(_cache_total),
            "last_duration": _last_duration,
            "last_success": _last_success,
            "site_counts": dict(_site_counts),
        }


def reset_for_tests():
    global _cache, _fetched_at, _retry_after, _last_duration, _last_success, _site_counts
    with _state_lock:
        _cache = None
        _fetched_at = 0.0
        _retry_after = 0.0
        _refresh_total.clear()
        _request_total.clear()
        _cache_total.clear()
        _last_duration = 0.0
        _last_success = 0.0
        _site_counts = {"systems": 0, "pairs": 0, "poles": 0}
