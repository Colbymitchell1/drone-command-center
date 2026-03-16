import math
from typing import List, Tuple

LatLon = Tuple[float, float]  # (lat, lon)

_METERS_PER_LAT_DEG = 111_320.0


def _meters_per_lon_deg(lat: float) -> float:
    return _METERS_PER_LAT_DEG * math.cos(math.radians(lat))


def _to_local(origin: LatLon, point: LatLon) -> Tuple[float, float]:
    """Returns (x=east_m, y=north_m) relative to origin."""
    x = (point[1] - origin[1]) * _meters_per_lon_deg(origin[0])
    y = (point[0] - origin[0]) * _METERS_PER_LAT_DEG
    return (x, y)


def _to_latlon(origin: LatLon, xy: Tuple[float, float]) -> LatLon:
    """Convert (x=east_m, y=north_m) back to lat/lon."""
    lat = origin[0] + xy[1] / _METERS_PER_LAT_DEG
    lon = origin[1] + xy[0] / _meters_per_lon_deg(origin[0])
    return (lat, lon)


def _x_intersections_at_y(polygon_xy: List[Tuple[float, float]], y: float) -> List[float]:
    """X-coordinates where the horizontal scanline at y crosses polygon edges."""
    xs = []
    n = len(polygon_xy)
    for i in range(n):
        x1, y1 = polygon_xy[i]
        x2, y2 = polygon_xy[(i + 1) % n]
        if y1 == y2:
            continue  # skip horizontal edges
        if min(y1, y2) <= y < max(y1, y2):
            x = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            xs.append(x)
    return sorted(xs)


def generate_lawnmower(polygon: List[LatLon], spacing_m: float = 20.0) -> List[LatLon]:
    """
    Generate boustrophedon (lawnmower) waypoints that cover a polygon.

    Scans west→east legs spaced spacing_m apart in the north direction,
    alternating direction each row to form the mowing pattern.

    Args:
        polygon: Closed polygon as list of (lat, lon) vertices.
        spacing_m: Distance between parallel legs in metres.

    Returns:
        Ordered list of (lat, lon) waypoints.
    """
    if len(polygon) < 3:
        return []

    origin = polygon[0]
    local = [_to_local(origin, p) for p in polygon]

    y_vals = [p[1] for p in local]
    y_min, y_max = min(y_vals), max(y_vals)

    waypoints_local: List[Tuple[float, float]] = []
    y = y_min + spacing_m / 2
    leg = 0

    while y <= y_max:
        xs = _x_intersections_at_y(local, y)
        # Pair up entry/exit intersections
        for i in range(0, len(xs) - 1, 2):
            x_start, x_end = xs[i], xs[i + 1]
            if leg % 2 == 0:
                waypoints_local.extend([(x_start, y), (x_end, y)])
            else:
                waypoints_local.extend([(x_end, y), (x_start, y)])
            leg += 1
        y += spacing_m

    return [_to_latlon(origin, wp) for wp in waypoints_local]
