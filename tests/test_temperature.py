import datetime as dt
import io
import json
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

import app
import temperature as weather
from app import WebSettings, create_app
from generate_live_map import build_html

NOW = 1788550000


def payload(region="forest"):
    return [{"elevation": 1650, "current_units": {"temperature_2m": "°F", "relative_humidity_2m": "%"},
             "current": {"temperature_2m": 54.2, "relative_humidity_2m": 63, "time": NOW - 300},
             "hourly_units": {"temperature_2m": "°F"},
             "hourly": {"time": [NOW + 3600, NOW + 7200],
                        "temperature_2m": [53.0, 51.5]}}
            for _ in weather.SAMPLE_POINTS[region]]


@pytest.fixture(autouse=True)
def clean_cache(monkeypatch):
    weather._cache.clear()
    weather._retry_after.clear()
    monkeypatch.delenv("OPEN_METEO_API_KEY", raising=False)
    monkeypatch.setattr(weather.time, "time", lambda: NOW)
    monkeypatch.setattr(weather, "load_station_observations", lambda _region, _now: ([], {"success": 0, "invalid": 0, "failure": 0}))


@pytest.mark.parametrize("region", ["forest", "malibu"])
def test_samples_form_a_broad_terrain_grid(region):
    points = weather.SAMPLE_POINTS[region]
    assert len(points) >= 50
    assert len({latitude for _name, latitude, _longitude in points}) >= 5
    assert len({longitude for _name, _latitude, longitude in points}) >= 8
    assert all(
        "road" not in name.casefold() and "highway" not in name.casefold()
        for name, _latitude, _longitude in points
        if name not in weather.PRIORITY_POINT_NAMES
        and name not in weather.ROAD_POINT_NAMES
    )


def test_named_landmark_samples_are_priority_points():
    expected_forest_names = [
        "Newcomb's Ranch",
        "Highway 39 lower canyon",
        "Highway 39 upper canyon",
        "GMR / GRR junction",
        "Glendora Ridge Road east",
        "Mount Baldy Road upper canyon",
    ]
    assert [point[0] for point in weather.SAMPLE_POINTS["forest"][:6]] == expected_forest_names
    assert weather.SAMPLE_POINTS["malibu"][0] == (
        "Rock Store / Old Place area", 34.112087, -118.783186
    )
    for region in ("forest", "malibu"):
        result = weather.parse_estimates(payload(region), region, NOW)
        priority_count = len(weather.PRIORITY_POINTS[region])
        assert all(point["priority"] is True for point in result["points"][:priority_count])
        assert all(point["road"] is True for point in result["points"][:priority_count])
        assert all(point["priority"] is False for point in result["points"][priority_count:])


def test_road_samples_form_the_baseline_in_both_regions():
    assert len(weather.ROAD_POINTS["forest"]) >= 70
    assert len(weather.ROAD_POINTS["malibu"]) >= 19
    assert {
        "Yerba Buena Road lower canyon",
        "Yerba Buena Road upper canyon",
        "Little Sycamore Canyon Road",
        "Mulholland Highway west",
    }.issubset({point[0] for point in weather.ROAD_POINTS["malibu"]})
    for region in ("forest", "malibu"):
        names = {name for name, _latitude, _longitude in weather.ROAD_POINTS[region]}
        result = weather.parse_estimates(payload(region), region, NOW)
        road_points = [point for point in result["points"] if point["name"] in names]
        assert {point["name"] for point in road_points} == names
        assert all(point["road"] is True for point in road_points)
        assert all(point["priority"] is False for point in road_points)


@pytest.mark.parametrize("region", ["forest", "malibu"])
def test_coordinates_are_requested_points_and_elevation_is_provider_terrain(region):
    data = payload(region)
    data[0].update(latitude=0, longitude=0)
    result = weather.parse_estimates(data, region, NOW)
    assert result["points"][0]["latitude"] == weather.SAMPLE_POINTS[region][0][1]
    assert result["points"][0]["elevation_m"] == 1650
    assert all(p["kind"] == "estimate" for p in result["points"])
    assert result["points"][0]["relative_humidity_percent"] == 63
    assert result["points"][0]["forecast"] == [
        {"valid_at": dt.datetime.fromtimestamp(NOW + 3600, dt.timezone.utc).isoformat(), "temperature_f": 53.0},
        {"valid_at": dt.datetime.fromtimestamp(NOW + 7200, dt.timezone.utc).isoformat(), "temperature_f": 51.5},
    ]


@pytest.mark.parametrize("field,value", [("temperature_2m", None), ("temperature_2m", float("nan")),
                                        ("temperature_2m", 200), ("time", NOW - 3601),
                                        ("time", NOW + 901)])
def test_invalid_values_are_omitted(field, value):
    data = payload()
    baseline_count = len(weather.parse_estimates(payload(), "forest", NOW)["points"])
    data[0]["current"][field] = value
    assert len(weather.parse_estimates(data, "forest", NOW)["points"]) == baseline_count - 1


def test_wrong_units_and_missing_elevation_are_not_displayed():
    data = payload()
    baseline_count = len(weather.parse_estimates(payload(), "forest", NOW)["points"])
    data[0]["current_units"]["temperature_2m"] = "°C"
    data[1]["elevation"] = None
    assert len(weather.parse_estimates(data, "forest", NOW)["points"]) == baseline_count - 2
    with pytest.raises(weather.TemperatureUnavailable):
        weather.parse_estimates([], "forest", NOW)


@pytest.mark.parametrize("region", ["forest", "malibu"])
def test_only_topographic_extremes_remain_off_road(region):
    data = payload(region)
    for index, row in enumerate(data):
        row["elevation"] = 100 + index
    result = weather.parse_estimates(data, region, NOW)
    terrain = [point for point in result["points"] if not point["road"]]
    assert 4 <= len(terrain) <= weather.TERRAIN_GRID_COLUMNS * weather.TERRAIN_GRID_ROWS
    high_count = sum(point["terrain_extreme"] == "high" for point in terrain)
    low_count = sum(point["terrain_extreme"] == "low" for point in terrain)
    assert abs(high_count - low_count) <= 1
    assert {point["name"] for point in terrain} == {
        "Local topographic high", "Local topographic low"
    }


def test_fresh_nws_station_observation_is_measured():
    station = weather.OBSERVATION_STATIONS["forest"][0]
    timestamp = dt.datetime.fromtimestamp(NOW - 600, dt.timezone.utc).isoformat()
    point = weather.parse_station_observation({
        "properties": {
            "timestamp": timestamp,
            "temperature": {"value": 20.0, "unitCode": "wmoUnit:degC"},
            "relativeHumidity": {"value": 47.4, "unitCode": "wmoUnit:percent"},
        }
    }, station, NOW)
    assert point == {
        "name": "Chilao RAWS", "station_id": "CHOC1",
        "latitude": 34.33167, "longitude": -118.03028,
        "temperature_f": 68.0, "elevation_m": 1661.16,
        "valid_at": timestamp, "kind": "observation",
        "priority": False, "road": False,
        "relative_humidity_percent": 47,
    }


def test_nearby_station_calibrates_current_estimate_with_distance_fade():
    estimates = [
        {"name": "Near", "latitude": 34.33, "longitude": -118.00,
         "elevation_m": 1643, "temperature_f": 72.0},
        {"name": "Far", "latitude": 34.33, "longitude": -117.80,
         "elevation_m": 1643, "temperature_f": 72.0},
    ]
    observations = [
        {"name": "Chilao RAWS", "latitude": 34.33167, "longitude": -118.03028,
         "elevation_m": 1661.16, "temperature_f": 79.0}
    ]
    weather.calibrate_estimates(estimates, observations)
    assert 75.0 <= estimates[0]["temperature_f"] <= 76.0
    assert estimates[0]["raw_temperature_f"] == 72.0
    assert estimates[0]["calibrated_by"] == ["Chilao RAWS"]
    assert estimates[1]["temperature_f"] == 72.0
    assert "calibrated_by" not in estimates[1]


def test_station_calibration_rejects_large_elevation_difference():
    estimates = [{"name": "Canyon", "latitude": 34.33, "longitude": -118.00,
                  "elevation_m": 1200, "temperature_f": 72.0}]
    observations = [{"name": "Ridge station", "latitude": 34.331, "longitude": -118.001,
                     "elevation_m": 1661, "temperature_f": 79.0}]
    weather.calibrate_estimates(estimates, observations)
    assert estimates[0]["temperature_f"] == 72.0
    assert "calibrated_by" not in estimates[0]


def test_station_observation_remains_available_between_reports():
    station = weather.OBSERVATION_STATIONS["forest"][0]
    timestamp = dt.datetime.fromtimestamp(NOW - 7200, dt.timezone.utc).isoformat()
    point = weather.parse_station_observation({
        "properties": {
            "timestamp": timestamp,
            "temperature": {"unitCode": "wmoUnit:degC", "value": 20.0},
        }
    }, station, NOW)

    assert point is not None


@pytest.mark.parametrize("temperature,timestamp,unit", [
    (None, NOW - 60, "wmoUnit:degC"),
    (20, NOW - weather.OBSERVATION_MAX_AGE_SECONDS - 1, "wmoUnit:degC"),
    (20, NOW - 60, "wmoUnit:degF"),
])
def test_invalid_nws_station_observation_is_omitted(temperature, timestamp, unit):
    iso = dt.datetime.fromtimestamp(timestamp, dt.timezone.utc).isoformat()
    payload = {"properties": {"timestamp": iso, "temperature": {
        "value": temperature, "unitCode": unit,
    }}}
    assert weather.parse_station_observation(
        payload, weather.OBSERVATION_STATIONS["forest"][0], NOW
    ) is None


def test_station_observations_augment_estimates(monkeypatch):
    observed = {
        "name": "Chilao RAWS", "station_id": "CHOC1",
        "latitude": 34.33167, "longitude": -118.03028,
        "temperature_f": 68.0, "elevation_m": 1661.16,
        "valid_at": dt.datetime.fromtimestamp(NOW - 60, dt.timezone.utc).isoformat(),
        "kind": "observation", "priority": False, "road": False,
    }
    monkeypatch.setattr(weather, "load_station_observations", lambda _region, _now: ([observed], {"success": 1, "invalid": 0, "failure": 0}))
    monkeypatch.setattr(weather, "urlopen", lambda *_args, **_kwargs: io.BytesIO(
        json.dumps(payload()).encode()
    ))
    result = weather.load_temperatures("forest")
    assert result["points"][0] == observed
    assert result["sources"] == ["NWS", "Open-Meteo"]


def test_cache_batches_and_key_stays_in_server_request(monkeypatch):
    requests = []
    def fetch(request, timeout):
        requests.append(request.full_url)
        return io.BytesIO(json.dumps(payload()).encode())
    monkeypatch.setattr(weather, "urlopen", fetch)
    monkeypatch.setenv("OPEN_METEO_API_KEY", "synthetic-test-key")
    first = weather.load_temperatures("forest")
    assert weather.load_temperatures("forest") == first
    assert len(requests) == 1
    url = urlsplit(requests[0])
    params = parse_qs(url.query)
    assert url.hostname == "customer-api.open-meteo.com"
    assert "elevation" not in params
    assert params["cell_selection"] == ["land"]
    assert params["hourly"] == ["temperature_2m"]
    assert params["current"] == ["temperature_2m,relative_humidity_2m"]
    assert params["forecast_hours"] == [str(weather.FORECAST_HOURS)]
    assert len(params["latitude"][0].split(",")) == len(weather.SAMPLE_POINTS["forest"])
    assert "synthetic-test-key" not in json.dumps(first)
    monkeypatch.setattr(weather.time, "time", lambda: NOW + 901)
    weather.load_temperatures("forest")
    assert len(requests) == 2


def test_failure_backoff_and_no_secret_error(monkeypatch):
    calls = []
    def fail(*args, **kwargs):
        calls.append(1)
        raise OSError("synthetic-secret-url")
    monkeypatch.setattr(weather, "urlopen", fail)
    for _ in range(2):
        with pytest.raises(weather.TemperatureUnavailable) as exc:
            weather.load_temperatures("forest")
        assert "secret" not in str(exc.value)
    assert len(calls) == 1


@pytest.mark.parametrize("region", ["forest", "malibu"])
def test_endpoint_and_local_render(tmp_path, monkeypatch, region):
    result = weather.parse_estimates(payload(region), region, NOW)
    monkeypatch.setattr(app, "load_temperatures", lambda requested: result if requested == region else None)
    with TestClient(create_app(WebSettings(database=tmp_path / "map.sqlite", base_path="/map"))) as client:
        response = client.get(f"/map/api/v1/temperature?region={region}")
        assert response.status_code == 200
        assert response.json() == result
        assert response.headers["Cache-Control"] == "public, max-age=60"
        assert client.head(f"/map/api/v1/temperature?region={region}").content == b""
        def fail(_):
            raise weather.TemperatureUnavailable()
        monkeypatch.setattr(app, "load_temperatures", fail)
        assert client.get("/api/v1/temperature").status_code == 503
    rendered = build_html([], "2026-09-04T12:00:00-07:00", 72, region=region, base_path="/map")
    assert 'const temperatureEndpoint = "/map/api/v1/temperature"' in rendered
    assert "__TEMPERATURE_ENDPOINT__" not in rendered
    assert "temperature-label" in rendered
    assert "orderedPoints" in rendered
    assert "displayRank" in rendered
    assert '!point.road && !point.terrain_extreme' in rendered
    assert "function placeTemperatureLabel" in rendered
    assert "function temperatureBand" in rendered
    assert "function shouldSkipTemperature" in rendered
    assert "duplicate: 130" in rendered
    assert 'point.name === "Newcomb\'s Ranch" ? "🌲"' in rendered
    assert 'point.name === "Rock Store / Old Place area" ? "🪨"' in rendered
    assert "if (!point.priority && shouldSkipTemperature" in rendered
    assert "if (!placement && point.priority)" in rendered
    assert 'class="temperature-landmark-icon"' in rendered
    assert 'if (degrees < 85) return "warm"' in rendered
    assert 'if (degrees < 105) return "very-hot"' in rendered
    assert 'return "extreme"' in rendered
    assert "function anchorGeometry" not in rendered
    assert 'class="temperature-anchor"' not in rendered
    assert 'class="temperature-terrain-symbol"' not in rendered
    assert "const height = 15" in rendered
    assert "degrees >= 100 ? 28 : 25" in rendered
    assert "previousPlacements.get(key)" in rendered
    assert "temperature-leader" not in rendered
    assert "occupied.push(placement.box)" in rendered
    assert 'maxWidth: 280, offset: [0, -14]' in rendered
    assert "Roads + terrain highs/lows" in rendered
    assert "Loading temperatures…" in rendered
    assert "Temperatures unavailable · Tap to retry" in rendered
    assert "left: 50%; top: 54px" in rendered
    assert 'state === "loading" && !points.length' in rendered
    assert "transform: translateX(-50%)" in rendered
    assert "const ageProgress = measured" in rendered
    assert "marker.setOpacity(1 - (0.40 * ageProgress))" in rendered
    assert "grayscale(${Math.round(ageProgress * 100)}%)" in rendered
    assert "National Weather Service" in rendered
    assert "Nearby modeled forecast" in rendered
    assert "Adjusted using" in rendered
    assert "% humidity" in rendered
    assert 'class="temperature-popup__forecast"' in rendered
    assert 'class="temperature-popup__heading"' in rendered
    assert 'class="temperature-popup__source-note"' in rendered
    assert 'autoPanPaddingBottomRight: [24, 74]' in rendered
    assert '@media (max-width: 520px)' in rendered
    assert "slice(0, 6)" in rendered
    assert "repeat(6, minmax(0, 1fr))" in rendered
    assert "rgba(254,226,226,.97)" in rendered
    assert "markers.forEach(marker => protectPoint(marker, 23))" in rendered
    assert "iconAnchor: [0, 0]" in rendered
    assert "Temperature estimates:" not in rendered


def test_cached_points_expire_by_model_time(monkeypatch):
    data = payload()
    for row in data:
        row["current"]["time"] = NOW - 3590
    monkeypatch.setattr(weather, "urlopen", lambda *a, **k: io.BytesIO(json.dumps(data).encode()))
    weather.load_temperatures("forest")
    monkeypatch.setattr(weather.time, "time", lambda: NOW + 20)
    with pytest.raises(weather.TemperatureUnavailable):
        weather.load_temperatures("forest")
