FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt /app/
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY map_workspace_ui.py temperature.py temperature_ui.py road_weather.py road_weather_ui.py weather_metrics.py app.py admin_sessions.py aircraft_tracking.py comments.py media.py mile_markers.py push_notifications.py ecs_logging.py geo_bounds.py incident_filters.py incident_marker_icons.py manage_comments.py scrape_chp_traffic.py scrape_wildweb_incidents.py generate_live_map.py serve_live_map.py speed_camera_ui.py /app/
COPY licenses/material-symbols/LICENSE /app/licenses/material-symbols/LICENSE
COPY licenses/font-awesome/ATTRIBUTION.md /app/licenses/font-awesome/ATTRIBUTION.md

EXPOSE 8080

CMD ["sh", "-c", "exec gunicorn app:app -k uvicorn.workers.UvicornWorker --workers ${WEB_WORKERS:-1} --bind ${HTTP_HOST:-0.0.0.0}:${HTTP_PORT:-8080} --access-logfile /dev/null --error-logfile -"]
