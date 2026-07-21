"""
routing.py
Free, no-API-key services used for this assessment:
  * Geocoding: OpenStreetMap Nominatim (https://nominatim.openstreetmap.org)
  * Routing:   OSRM public demo server (https://router.project-osrm.org)

Both are rate-limited public demo instances. For production use you'd
point ROUTING/GEOCODING at your own hosted instance or a paid provider,
but no key is required for this project.
"""
import requests

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OSRM_URL = "https://router.project-osrm.org/route/v1/driving"

HEADERS = {"User-Agent": "eld-trip-planner-assessment/1.0"}


class GeocodeError(Exception):
    pass


class RoutingError(Exception):
    pass


def geocode(place: str):
    """Return (lat, lon, display_name) for a free-text place string."""
    resp = requests.get(
        NOMINATIM_URL,
        params={"q": place, "format": "json", "limit": 1},
        headers=HEADERS,
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data:
        raise GeocodeError(f"Could not find a location for '{place}'.")
    item = data[0]
    return float(item["lat"]), float(item["lon"]), item.get("display_name", place)


def route(origin_latlon, dest_latlon):
    """origin_latlon / dest_latlon: (lat, lon). Returns
    {distance_miles, duration_hours, geometry: [[lon,lat], ...]}"""
    coord_str = f"{origin_latlon[1]},{origin_latlon[0]};{dest_latlon[1]},{dest_latlon[0]}"
    url = f"{OSRM_URL}/{coord_str}"
    resp = requests.get(
        url,
        params={"overview": "full", "geometries": "geojson"},
        headers=HEADERS,
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != "Ok" or not data.get("routes"):
        raise RoutingError("Could not compute a driving route between those two points.")
    r = data["routes"][0]
    return {
        "distance_miles": r["distance"] / 1609.344,
        "duration_hours": r["duration"] / 3600.0,
        "geometry": r["geometry"]["coordinates"],
    }
