"""Shared incident exclusions for collection and display."""

import re


I210_ROAD = re.compile(
    r"\b(?:i\s*-?\s*210|interstate\s+210|(?:ca|sr|hwy|highway)\s*-?\s*210|"
    r"210\s+(?:fwy|freeway)|foothill\s+(?:fwy|freeway))\b",
    re.IGNORECASE,
)
SR2_ROAD = re.compile(r"\b(?:sr|ca|hwy|highway)\s*-?\s*2\b", re.IGNORECASE)
SR2_CITY_CROSS_STREET = re.compile(r"\b(?:verdugo|foothill)\s+(?:blvd|boulevard)\b", re.IGNORECASE)


def is_nearby_forest_highway_incident(incident):
    """Exclude freeway incidents and the city segment of SR-2 near the forest."""
    if (incident.get("source") or "chp") != "chp":
        return False
    location = str(incident.get("location") or "")
    primary = location.split("/", 1)[0].strip()
    description = str(incident.get("location_desc") or "")

    if I210_ROAD.search(primary):
        return True
    if I210_ROAD.search(location) and re.search(r"\b(?:con|connector|ramp)\b", location, re.IGNORECASE):
        return True
    if SR2_ROAD.search(primary) and SR2_CITY_CROSS_STREET.search(f"{location} {description}"):
        return True
    return False
