"""
MAVSDK geofence + mission upload helpers.

Both coroutines are designed to run on the DroneConnector asyncio loop
via asyncio.run_coroutine_threadsafe().
"""
from typing import List, Tuple

from mavsdk import System
from mavsdk.geofence import GeofenceData, Point as GeoPoint, Polygon as GeoPolygon, FenceType
from mavsdk.mission import MissionItem, MissionPlan

LatLon = Tuple[float, float]

_CRUISE_ALT_M = 30.0
_CRUISE_SPEED_MS = 5.0


async def upload_geofence(drone: System, polygon: List[LatLon]) -> None:
    points = [GeoPoint(lat, lon) for lat, lon in polygon]
    fence = GeoPolygon(points, FenceType.INCLUSION)
    await drone.geofence.upload_geofence(GeofenceData([fence], []))


async def upload_mission(drone: System, waypoints: List[LatLon]) -> None:
    nan = float("nan")
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
            loiter_time_s=nan,
            camera_photo_interval_s=nan,
            acceptance_radius_m=nan,
            yaw_deg=nan,
            camera_photo_distance_m=nan,
            vehicle_action=MissionItem.VehicleAction.NONE,
        )
        for lat, lon in waypoints
    ]
    await drone.mission.upload_mission(MissionPlan(items))
