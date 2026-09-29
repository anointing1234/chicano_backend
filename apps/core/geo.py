"""
Lightweight geo maths (no PostGIS required to get started).

* `haversine_km` straight-line distance between two lat/lng points.
* `estimate_route` distance + duration for a trip. It uses a road factor over the
  straight line and an average Lagos speed per service. This is a placeholder:
  swap `estimate_route` for Google Distance Matrix / Directions later without
  touching any caller.

When the fleet grows, move provider search to PostGIS (a GiST index on a PointField)
or Redis GEO. Only `apps.rides.dispatch.find_candidates` would change.
"""
from math import asin, cos, radians, sin, sqrt

EARTH_RADIUS_KM = 6371.0
ROAD_FACTOR = 1.35                      # roads are longer than the straight line
AVG_SPEED_KMH = {"car": 22.0, "bike": 30.0}   # Lagos average in traffic (tune with real data)


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    lat1, lng1, lat2, lng2 = map(radians, (float(lat1), float(lng1), float(lat2), float(lng2)))
    a = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lng2 - lng1) / 2) ** 2
    return 2 * EARTH_RADIUS_KM * asin(sqrt(a))


def estimate_route(points: list[tuple[float, float]], service: str) -> tuple[int, int]:
    """
    Return (distance_m, duration_s) for a path through `points`
    (pickup, optional stops..., drop-off).
    """
    km = sum(haversine_km(*points[i], *points[i + 1]) for i in range(len(points) - 1)) * ROAD_FACTOR
    hours = km / AVG_SPEED_KMH.get(service, 22.0)
    return int(km * 1000), int(hours * 3600)


def eta_seconds(km: float, service: str) -> int:
    """Rough time for a provider to reach the pickup."""
    return int(km * ROAD_FACTOR / AVG_SPEED_KMH.get(service, 22.0) * 3600)
