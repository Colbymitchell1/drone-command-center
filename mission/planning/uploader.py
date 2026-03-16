"""
MAVSDK geofence + mission upload helpers.

Both coroutines are designed to run on the DroneConnector asyncio loop
via asyncio.run_coroutine_threadsafe().
"""
import math
from typing import List, Tuple

from mavsdk import System
from mavsdk.geofence import GeofenceData, Point as GeoPoint, Polygon as GeoPolygon, FenceType
from mavsdk.mission import MissionItem, MissionPlan

LatLon = Tuple[float, float]

_CRUISE_ALT_M = 10.0
_CRUISE_SPEED_MS = 5.0
_ACCEPTANCE_RADIUS_M = 2.0   # how close the drone must get before accepting a waypoint
_LOITER_TIME_S = 0.0         # no pause at waypoints; NaN can stall PX4 indefinitely


def _haversine_m(a: LatLon, b: LatLon) -> float:
    """Approximate ground distance between two lat/lon points in metres."""
    R = 6_371_000
    lat1, lat2 = math.radians(a[0]), math.radians(b[0])
    dlat = math.radians(b[0] - a[0])
    dlon = math.radians(b[1] - a[1])
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


async def upload_geofence(drone: System, polygon: List[LatLon]) -> None:
    points = [GeoPoint(lat, lon) for lat, lon in polygon]
    fence = GeoPolygon(points, FenceType.INCLUSION)
    await drone.geofence.upload_geofence(GeofenceData([fence], []))


async def upload_mission(drone: System, waypoints: List[LatLon]) -> None:
    nan = float("nan")

    # ── debug: print waypoint list so spacing can be verified in the console ──
    print(f"[uploader] Uploading {len(waypoints)} waypoints:")
    for i, (lat, lon) in enumerate(waypoints):
        dist = ""
        if i > 0:
            d = _haversine_m(waypoints[i - 1], (lat, lon))
            dist = f"  (+{d:.1f} m from prev)"
        print(f"  WP{i+1:02d}  lat={lat:.6f}  lon={lon:.6f}{dist}")

    items = [
        MissionItem(
            latitude_deg=lat,
            longitude_deg=lon,
            relative_altitude_m=_CRUISE_ALT_M,
            speed_m_s=_CRUISE_SPEED_MS,
            is_fly_through=True,
            gimbal_pitch_deg=nan,
            gimbal_yaw_deg=nan,
            camera_action=MissionItem.CameraAction.NONE,
            loiter_time_s=_LOITER_TIME_S,
            camera_photo_interval_s=nan,
            acceptance_radius_m=_ACCEPTANCE_RADIUS_M,
            yaw_deg=nan,
            camera_photo_distance_m=nan,
            vehicle_action=MissionItem.VehicleAction.NONE,
        )
        for lat, lon in waypoints
    ]
    await drone.mission.upload_mission(MissionPlan(items))
    print(f"[uploader] Mission upload complete.")
