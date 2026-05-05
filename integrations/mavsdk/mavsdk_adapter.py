"""
integrations/mavsdk/mavsdk_adapter.py

MAVSDKVehicleAdapter -- concrete implementation of VehicleAdapter for
PX4/ArduPilot vehicles connected via MAVSDK (UDP, serial, TCP).

This is the only file in the project that imports mavsdk.
Everything above this layer speaks domain types only.
"""

from __future__ import annotations

import asyncio
import math
import threading
from datetime import datetime, timezone
from typing import Callable, List, Optional

from app.models.domain import (
    CheckStatus,
    CommandResult,
    CommandStatus,
    FlightMode,
    GpsFixType,
    MissionPlan,
    MissionStatus,
    MissionValidationResult,
    PreflightCheck,
    PreflightResult,
    VehicleCapabilities,
    VehicleHealth,
    VehicleIdentity,
    VehicleState,
    Waypoint,
)
from integrations.vehicle_adapter import Subscription, VehicleAdapter

# MAVSDK imports -- contained to this file only
from mavsdk import System
from mavsdk.geofence import FenceType
from mavsdk.geofence import Point as GeoPoint
from mavsdk.geofence import Polygon as GeoPolygon
from mavsdk.geofence import GeofenceData
from mavsdk.mission import MissionItem
from mavsdk.mission import MissionPlan as MAVMissionPlan

# ---------------------------------------------------------------------------
# Flight mode mapping: MAVSDK enum name → domain FlightMode
# ---------------------------------------------------------------------------

_FLIGHT_MODE_MAP: dict[str, FlightMode] = {
    "MANUAL":       FlightMode.MANUAL,
    "STABILIZED":   FlightMode.STABILIZED,
    "ALTCTL":       FlightMode.STABILIZED,
    "POSCTL":       FlightMode.STABILIZED,
    "MISSION":      FlightMode.MISSION,
    "RTL":          FlightMode.RTL,
    "LAND":         FlightMode.LAND,
    "HOLD":         FlightMode.HOLD,
    "OFFBOARD":     FlightMode.OFFBOARD,
    "TAKEOFF":      FlightMode.TAKEOFF,
    "UNKNOWN":      FlightMode.UNKNOWN,
}

_GPS_FIX_MAP: dict[int, GpsFixType] = {
    0: GpsFixType.NO_GPS,
    1: GpsFixType.NO_FIX,
    2: GpsFixType.FIX_2D,
    3: GpsFixType.FIX_3D,
    4: GpsFixType.FIX_DGPS,
    5: GpsFixType.RTK_FLOAT,
    6: GpsFixType.RTK_FIXED,
}

# Minimum GPS fix int value considered acceptable for flight
_MIN_FIX_INT = 3


# ---------------------------------------------------------------------------
# MAVSDKVehicleAdapter
# ---------------------------------------------------------------------------


class MAVSDKVehicleAdapter(VehicleAdapter):
    """
    Wraps MAVSDK System behind the VehicleAdapter interface.

    Threading model:
        A dedicated daemon thread runs a private asyncio event loop.
        All MAVSDK coroutines execute on that loop.
        Public async methods bridge from the caller's context via
        asyncio.run_coroutine_threadsafe() when called from Qt threads,
        or directly awaited when called from another async context on the
        same loop.

    Usage (from Qt main thread):
        adapter = MAVSDKVehicleAdapter(vehicle_id="drone-1")
        future = asyncio.run_coroutine_threadsafe(
            adapter.connect("udpin://0.0.0.0:14540"), adapter.loop
        )
        result = future.result(timeout=30)
    """

    # Telemetry publish rate (Hz) -- how often subscribe_state callbacks fire
    _DEFAULT_HZ = 4.0

    def __init__(self, vehicle_id: str = "drone-1"):
        self._vehicle_id = vehicle_id
        self._drone: Optional[System] = None
        self._connected = False
        self._geofence_uploaded = False

        # Internal telemetry state -- mutated by stream tasks, read by publish loop
        self._state_raw: dict = {
            "lat":          None,
            "lon":          None,
            "alt_amsl":     None,
            "alt_relative": None,
            "heading_deg":  None,
            "ground_speed": None,
            "vert_speed":   None,
            "battery_pct":  None,
            "battery_v":    None,
            "is_sim_batt":  False,
            "flight_mode":  FlightMode.UNKNOWN,
            "armed":        None,
            "in_air":       None,
            "gps_fix":      GpsFixType.NO_FIX,
            "satellites":   None,
            "mission_item": None,
            "failsafe":     False,
        }

        self._stream_tasks: list[asyncio.Task] = []
        self._publish_callbacks: list[Callable[[VehicleState], None]] = []
        self._publish_hz: float = self._DEFAULT_HZ

        # Private event loop on a daemon thread
        self._loop = asyncio.new_event_loop()
        t = threading.Thread(
            target=self._run_loop, daemon=True, name=f"mavsdk-{vehicle_id}"
        )
        t.start()

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        """Expose the internal loop for run_coroutine_threadsafe callers."""
        return self._loop

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    # ── Identity and capabilities ────────────────────────────────────────────

    async def get_identity(self) -> VehicleIdentity:
        return VehicleIdentity(
            vehicle_id=self._vehicle_id,
            adapter_type="MAVSDKVehicleAdapter",
            firmware="unknown",       # populated after connection if needed
            vehicle_type="unknown",
            autopilot="PX4/ArduPilot",
        )

    async def get_capabilities(self) -> VehicleCapabilities:
        return VehicleCapabilities(
            supports_mission_upload=True,
            supports_geofence=True,
            supports_rtl=True,
            supports_pause_resume=True,
            supports_guided_goto=False,
            supports_camera_trigger=False,
            supports_battery_status=True,
            supports_health_checks=True,
            supports_log_download=False,
            supports_remote_id_status=False,
            supports_offboard_velocity=True,
        )

    # ── Connection lifecycle ─────────────────────────────────────────────────

    async def connect(self, connection_string: str = "udpin://0.0.0.0:14540") -> CommandResult:
        try:
            self._drone = System()
            await self._drone.connect(system_address=connection_string)

            async for state in self._drone.core.connection_state():
                if state.is_connected:
                    self._connected = True
                    self._start_streams()
                    return CommandResult.success(
                        f"Connected to vehicle at {connection_string}"
                    )

            return CommandResult.failed(
                "Connection loop ended without connecting",
                code="CONNECT_NO_STATE",
            )

        except Exception as e:
            return CommandResult.failed(
                str(e), code="CONNECT_EXCEPTION", recoverable=True
            )

    async def disconnect(self) -> CommandResult:
        self._stop_streams()
        self._connected = False
        self._drone = None
        self._geofence_uploaded = False
        return CommandResult.success("Disconnected")

    def is_connected(self) -> bool:
        return self._connected

    # ── State and health ─────────────────────────────────────────────────────

    async def get_state(self) -> VehicleState:
        return self._build_state()

    async def get_health(self) -> VehicleHealth:
        """
        Query live vehicle health. Returns blocking items and warnings.
        Used by the preflight check flow before arming.
        """
        if not self._drone or not self._connected:
            return VehicleHealth(
                ready=False,
                blocking=["Vehicle not connected"],
            )

        blocking: list[str] = []
        warnings: list[str] = []

        # GPS check
        try:
            async def _first_gps():
                async for info in self._drone.telemetry.gps_info():
                    return info
            info = await asyncio.wait_for(_first_gps(), timeout=5.0)
            fix_int = info.fix_type.value if info else 0
            if fix_int < _MIN_FIX_INT:
                blocking.append(
                    f"GPS fix insufficient ({info.fix_type.name if info else 'NO_DATA'}) -- 3D fix required"
                )
        except asyncio.TimeoutError:
            blocking.append("GPS data not received within 5s")
        except Exception as e:
            warnings.append(f"GPS check error: {e}")

        # Home position check
        try:
            async def _first_home():
                async for h in self._drone.telemetry.home():
                    return h
            home = await asyncio.wait_for(_first_home(), timeout=5.0)
            if home is None:
                blocking.append("Home position not set")
        except asyncio.TimeoutError:
            blocking.append("Home position not received within 5s")
        except Exception as e:
            warnings.append(f"Home position check error: {e}")

        # Battery check
        try:
            async def _first_batt():
                async for b in self._drone.telemetry.battery():
                    return b
            batt = await asyncio.wait_for(_first_batt(), timeout=5.0)
            if batt is not None:
                raw = batt.remaining_percent
                if raw >= 0:
                    pct = raw * 100.0 if raw <= 1.0 else float(raw)
                    if pct < 20.0:
                        warnings.append(f"Battery low: {pct:.0f}%")
        except asyncio.TimeoutError:
            warnings.append("Battery data not received within 5s")
        except Exception as e:
            warnings.append(f"Battery check error: {e}")

        # Geofence check
        if not self._geofence_uploaded:
            warnings.append("No geofence uploaded this session")

        ready = len(blocking) == 0
        return VehicleHealth(ready=ready, blocking=blocking, warnings=warnings)

    # ── Subscriptions ────────────────────────────────────────────────────────

    def subscribe_state(
        self,
        callback: Callable[[VehicleState], None],
        hz: float = 4.0,
    ) -> Subscription:
        self._publish_callbacks.append(callback)
        self._publish_hz = hz

        def _cancel():
            if callback in self._publish_callbacks:
                self._publish_callbacks.remove(callback)

        return Subscription(_cancel)

    # ── Mission lifecycle ────────────────────────────────────────────────────

    async def validate_mission(self, plan: MissionPlan) -> MissionValidationResult:
        """Pure logic validation -- no vehicle connection required."""
        errors: list[str] = []
        warnings: list[str] = []

        waypoints = plan.geometry.waypoints
        if len(waypoints) < 2:
            errors.append(f"Mission requires at least 2 waypoints, got {len(waypoints)}")

        for i, wp in enumerate(waypoints):
            if not (-90 <= wp.lat <= 90):
                errors.append(f"Waypoint {i}: invalid latitude {wp.lat}")
            if not (-180 <= wp.lon <= 180):
                errors.append(f"Waypoint {i}: invalid longitude {wp.lon}")
            if wp.alt_m <= 0:
                errors.append(f"Waypoint {i}: altitude must be > 0m")
            if wp.alt_m > 120:  # ~400ft Part 107 limit
                warnings.append(
                    f"Waypoint {i}: altitude {wp.alt_m}m exceeds 120m (400ft) Part 107 limit"
                )

        if plan.regulatory.bvlos:
            warnings.append("Mission marked BVLOS -- ensure appropriate authorization")
        if plan.regulatory.night_operation:
            warnings.append("Night operation -- confirm anti-collision lighting")
        if plan.regulatory.over_people:
            warnings.append("Operation over people -- confirm Part 107 category compliance")

        return MissionValidationResult(
            valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
        )

    async def upload_mission(self, plan: MissionPlan) -> CommandResult:
        if not self._drone or not self._connected:
            return CommandResult.failed("Not connected", code="NOT_CONNECTED")

        try:
            # Upload geofence if polygon provided
            if plan.geometry.geofence_polygon:
                points = [
                    GeoPoint(lat, lon)
                    for lat, lon in plan.geometry.geofence_polygon
                ]
                fence = GeoPolygon(points, FenceType.INCLUSION)
                await self._drone.geofence.upload_geofence(GeofenceData([fence], []))
                self._geofence_uploaded = True

            # Build MAVSDK mission items from domain waypoints
            nan = float("nan")
            items = [
                MissionItem(
                    latitude_deg=wp.lat,
                    longitude_deg=wp.lon,
                    relative_altitude_m=wp.alt_m,
                    speed_m_s=wp.speed_mps,
                    is_fly_through=wp.is_fly_through,
                    gimbal_pitch_deg=nan,
                    gimbal_yaw_deg=nan,
                    camera_action=MissionItem.CameraAction.NONE,
                    loiter_time_s=0.0,
                    camera_photo_interval_s=nan,
                    acceptance_radius_m=wp.acceptance_radius,
                    yaw_deg=nan,
                    camera_photo_distance_m=nan,
                    vehicle_action=MissionItem.VehicleAction.NONE,
                )
                for wp in plan.geometry.waypoints
            ]

            await self._drone.mission.upload_mission(MAVMissionPlan(items))
            return CommandResult.success(
                f"Uploaded {len(items)} waypoints"
            )

        except Exception as e:
            return CommandResult.failed(
                str(e), code="MISSION_UPLOAD_ERROR", recoverable=True
            )

    async def start_mission(self) -> CommandResult:
        if not self._drone or not self._connected:
            return CommandResult.failed("Not connected", code="NOT_CONNECTED")
        try:
            await self._drone.action.arm()
            await self._drone.mission.start_mission()
            return CommandResult.success("Mission started")
        except Exception as e:
            return CommandResult.failed(str(e), code="START_MISSION_ERROR")

    async def pause_mission(self) -> CommandResult:
        if not self._drone or not self._connected:
            return CommandResult.failed("Not connected", code="NOT_CONNECTED")
        try:
            await self._drone.mission.pause_mission()
            return CommandResult.success("Mission paused")
        except Exception as e:
            return CommandResult.failed(str(e), code="PAUSE_MISSION_ERROR")

    async def resume_mission(self) -> CommandResult:
        if not self._drone or not self._connected:
            return CommandResult.failed("Not connected", code="NOT_CONNECTED")
        try:
            await self._drone.mission.start_mission()
            return CommandResult.success("Mission resumed")
        except Exception as e:
            return CommandResult.failed(str(e), code="RESUME_MISSION_ERROR")

    async def cancel_mission(self) -> CommandResult:
        if not self._drone or not self._connected:
            return CommandResult.failed("Not connected", code="NOT_CONNECTED")
        try:
            await self._drone.mission.clear_mission()
            return CommandResult.success("Mission cancelled")
        except Exception as e:
            return CommandResult.failed(str(e), code="CANCEL_MISSION_ERROR")

    # ── Safety commands ──────────────────────────────────────────────────────

    async def return_to_launch(self) -> CommandResult:
        if not self._drone or not self._connected:
            return CommandResult.failed("Not connected", code="NOT_CONNECTED")
        for attempt in range(3):
            try:
                await self._drone.action.return_to_launch()
                return CommandResult.success("RTL commanded")
            except Exception as e:
                if attempt == 2:
                    return CommandResult.failed(
                        str(e), code="RTL_ERROR", recoverable=False
                    )
                await asyncio.sleep(0.5)
        return CommandResult.failed("RTL failed after 3 attempts", code="RTL_EXHAUSTED")

    async def land(self) -> CommandResult:
        if not self._drone or not self._connected:
            return CommandResult.failed("Not connected", code="NOT_CONNECTED")
        try:
            await self._drone.action.land()
            return CommandResult.success("Landing commanded")
        except Exception as e:
            return CommandResult.failed(str(e), code="LAND_ERROR")

    # ── Internal: telemetry streams ──────────────────────────────────────────

    def _start_streams(self) -> None:
        """Start all telemetry stream tasks on the internal loop."""
        self._stream_tasks = [
            self._loop.create_task(self._stream_position(),    name="telem-position"),
            self._loop.create_task(self._stream_velocity(),    name="telem-velocity"),
            self._loop.create_task(self._stream_attitude(),    name="telem-attitude"),
            self._loop.create_task(self._stream_battery(),     name="telem-battery"),
            self._loop.create_task(self._stream_flight_mode(), name="telem-mode"),
            self._loop.create_task(self._stream_armed(),       name="telem-armed"),
            self._loop.create_task(self._stream_in_air(),      name="telem-in-air"),
            self._loop.create_task(self._stream_gps(),         name="telem-gps"),
            self._loop.create_task(self._stream_mission_progress(), name="telem-mission"),
            self._loop.create_task(self._publish_loop(),       name="telem-publish"),
        ]

    def _stop_streams(self) -> None:
        for task in self._stream_tasks:
            task.cancel()
        self._stream_tasks.clear()

    def _build_state(self) -> VehicleState:
        """Build a VehicleState snapshot from the current raw state dict."""
        r = self._state_raw
        return VehicleState(
            vehicle_id=self._vehicle_id,
            timestamp_utc=datetime.now(timezone.utc),
            lat=r["lat"],
            lon=r["lon"],
            alt_amsl=r["alt_amsl"],
            alt_relative=r["alt_relative"],
            heading_deg=r["heading_deg"],
            ground_speed_mps=r["ground_speed"],
            vertical_speed_mps=r["vert_speed"],
            battery_percent=r["battery_pct"],
            battery_voltage=r["battery_v"],
            is_sim_battery=r["is_sim_batt"],
            flight_mode=r["flight_mode"],
            armed=r["armed"],
            in_air=r["in_air"],
            gps_fix_type=r["gps_fix"],
            satellites=r["satellites"],
            current_mission_item=r["mission_item"],
            failsafe_active=r["failsafe"],
        )

    async def _publish_loop(self) -> None:
        """Emit VehicleState to all subscribers at the configured rate."""
        while True:
            interval = 1.0 / self._publish_hz
            if self._publish_callbacks and any(
                v is not None for v in self._state_raw.values()
            ):
                state = self._build_state()
                for cb in list(self._publish_callbacks):
                    try:
                        cb(state)
                    except Exception:
                        pass
            await asyncio.sleep(interval)

    # ── Stream coroutines ────────────────────────────────────────────────────

    async def _stream_position(self) -> None:
        try:
            async for pos in self._drone.telemetry.position():
                self._state_raw["lat"] = round(pos.latitude_deg, 6)
                self._state_raw["lon"] = round(pos.longitude_deg, 6)
                self._state_raw["alt_amsl"] = round(pos.absolute_altitude_m, 1)
                self._state_raw["alt_relative"] = round(pos.relative_altitude_m, 1)
        except asyncio.CancelledError:
            pass

    async def _stream_velocity(self) -> None:
        try:
            async for vel in self._drone.telemetry.velocity_ned():
                speed = math.sqrt(vel.north_m_s ** 2 + vel.east_m_s ** 2)
                self._state_raw["ground_speed"] = round(speed, 1)
                self._state_raw["vert_speed"] = round(vel.down_m_s, 1)
        except asyncio.CancelledError:
            pass

    async def _stream_attitude(self) -> None:
        try:
            async for att in self._drone.telemetry.attitude_euler():
                self._state_raw["heading_deg"] = round(att.yaw_deg % 360, 1)
        except asyncio.CancelledError:
            pass

    async def _stream_battery(self) -> None:
        try:
            async for batt in self._drone.telemetry.battery():
                raw = batt.remaining_percent
                if raw < 0:
                    self._state_raw["is_sim_batt"] = True
                    self._state_raw["battery_pct"] = None
                elif raw <= 1.0:
                    self._state_raw["battery_pct"] = round(raw * 100, 1)
                else:
                    self._state_raw["battery_pct"] = round(min(raw, 100.0), 1)
                if hasattr(batt, "voltage_v"):
                    self._state_raw["battery_v"] = round(batt.voltage_v, 2)
        except asyncio.CancelledError:
            pass

    async def _stream_flight_mode(self) -> None:
        try:
            async for mode in self._drone.telemetry.flight_mode():
                self._state_raw["flight_mode"] = _FLIGHT_MODE_MAP.get(
                    mode.name, FlightMode.UNKNOWN
                )
        except asyncio.CancelledError:
            pass

    async def _stream_armed(self) -> None:
        try:
            async for armed in self._drone.telemetry.armed():
                self._state_raw["armed"] = armed
        except asyncio.CancelledError:
            pass

    async def _stream_in_air(self) -> None:
        try:
            async for in_air in self._drone.telemetry.in_air():
                self._state_raw["in_air"] = in_air
        except asyncio.CancelledError:
            pass

    async def _stream_gps(self) -> None:
        try:
            async for info in self._drone.telemetry.gps_info():
                self._state_raw["gps_fix"] = _GPS_FIX_MAP.get(
                    info.fix_type.value, GpsFixType.NO_FIX
                )
                self._state_raw["satellites"] = info.num_satellites
        except asyncio.CancelledError:
            pass

    async def _stream_mission_progress(self) -> None:
        try:
            async for progress in self._drone.telemetry.mission_progress():
                self._state_raw["mission_item"] = progress.current
        except asyncio.CancelledError:
            pass
