import math
from typing import List, Tuple

LatLon  = Tuple[float, float]   # (lat, lon)
OffsetM = Tuple[float, float]   # (north_m, east_m) relative to some origin

_METERS_PER_LAT_DEG = 111_320.0


def _meters_per_lon_deg(lat: float) -> float:
    return _METERS_PER_LAT_DEG * math.cos(math.radians(lat))


def _to_local(origin: LatLon, point: LatLon) -> Tuple[float, float]:
    """Returns (east_m, north_m) relative to origin."""
    east  = (point[1] - origin[1]) * _meters_per_lon_deg(origin[0])
    north = (point[0] - origin[0]) * _METERS_PER_LAT_DEG
    return (east, north)


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


def polygon_center(polygon: List[LatLon]) -> LatLon:
    """Return the centroid of a polygon as (lat, lon)."""
    lat = sum(p[0] for p in polygon) / len(polygon)
    lon = sum(p[1] for p in polygon) / len(polygon)
    return (lat, lon)


def offsets_to_latlon(origin: LatLon, offsets: List[OffsetM]) -> List[LatLon]:
    """
    Place a list of (north_m, east_m) offsets at the given GPS origin.

    Args:
        origin:  (lat, lon) anchor point — e.g. drone's current position.
        offsets: List of (north_m, east_m) offsets from the polygon centroid
                 as returned by generate_lawnmower().

    Returns:
        Absolute (lat, lon) waypoints.
    """
    lat0, lon0 = origin
    result: List[LatLon] = []
    for north_m, east_m in offsets:
        lat = lat0 + north_m / _METERS_PER_LAT_DEG
        lon = lon0 + east_m  / _meters_per_lon_deg(lat0)
        result.append((lat, lon))
    return result


def generate_lawnmower(polygon: List[LatLon], spacing_m: float = 20.0) -> List[OffsetM]:
    """
    Generate boustrophedon (lawnmower) waypoints covering a polygon.

    Returns waypoints as (north_m, east_m) metre offsets relative to the
    polygon centroid — NOT absolute lat/lon.  This separates pattern shape
    from GPS placement so the same pattern can be re-anchored anywhere.

    Call offsets_to_latlon(origin, offsets) to place the pattern at any
    GPS coordinate (e.g. the drone's current position at upload time).

    Args:
        polygon:   Closed polygon as list of (lat, lon) vertices.
        spacing_m: Distance between parallel legs in metres.

    Returns:
        Ordered list of (north_m, east_m) offsets from the polygon centroid.
    """
    if len(polygon) < 3:
        return []

    center = polygon_center(polygon)
    # Convert polygon to local (east_m, north_m) coordinates centred on the centroid.
    # _to_local returns (east_m, north_m), so index 0 = east, index 1 = north.
    local = [_to_local(center, p) for p in polygon]

    y_vals = [p[1] for p in local]   # north values
    y_min, y_max = min(y_vals), max(y_vals)

    offsets: List[OffsetM] = []
    y = y_min + spacing_m / 2
    leg = 0

    while y <= y_max:
        xs = _x_intersections_at_y(local, y)   # east values at this north scanline
        for i in range(0, len(xs) - 1, 2):
            x_start, x_end = xs[i], xs[i + 1]
            if leg % 2 == 0:
                offsets.extend([(y, x_start), (y, x_end)])
            else:
                offsets.extend([(y, x_end), (y, x_start)])
            leg += 1
        y += spacing_m

    # Each entry is (north_m, east_m) from the polygon centroid.
    return offsets
