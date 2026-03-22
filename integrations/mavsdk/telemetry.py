import asyncio
import math

from mavsdk import System

from app.events.event_bus import bus


class TelemetryManager:
    """
    Subscribes to MAVSDK telemetry streams and publishes consolidated
    state via bus.telemetry_updated at a fixed 4 Hz rate.

    Each stream coroutine only mutates _state; a single publish loop
    handles emission so the Qt event queue isn't flooded by the 50 Hz
    attitude stream.

    bus.telemetry_updated is emitted from the asyncio thread — Qt delivers
    it to main-thread slots via a queued connection automatically.
    """

    _PUBLISH_HZ = 4

    def __init__(self, drone: System, loop: asyncio.AbstractEventLoop):
        self._drone = drone
        self._loop = loop
        self._tasks: list[asyncio.Task] = []
        self._state: dict = {
            "lat":         None,
            "lon":         None,
            "alt":         None,
            "speed":       None,
            "heading":     None,
            "battery":     None,
            "flight_mode": None,   # FlightMode enum name string e.g. "MISSION", "RTL"
            "armed":       None,   # bool
        }

    def start(self) -> None:
        """Schedule all stream tasks. Must be called from within the asyncio loop thread."""
        self._tasks = [
            self._loop.create_task(self._stream_position(),    name="telem-position"),
            self._loop.create_task(self._stream_velocity(),    name="telem-velocity"),
            self._loop.create_task(self._stream_attitude(),    name="telem-attitude"),
            self._loop.create_task(self._stream_battery(),     name="telem-battery"),
            self._loop.create_task(self._stream_flight_mode(), name="telem-flight-mode"),
            self._loop.create_task(self._stream_armed(),       name="telem-armed"),
            self._loop.create_task(self._publish_loop(),       name="telem-publish"),
        ]

    def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()

    # ── publish ───────────────────────────────────────────────────────────────

    async def _publish_loop(self) -> None:
        interval = 1.0 / self._PUBLISH_HZ
        while True:
            if any(v is not None for v in self._state.values()):
                bus.telemetry_updated.emit(dict(self._state))
            await asyncio.sleep(interval)

    # ── streams ───────────────────────────────────────────────────────────────

    async def _stream_position(self) -> None:
        try:
            async for pos in self._drone.telemetry.position():
                self._state["lat"] = round(pos.latitude_deg, 6)
                self._state["lon"] = round(pos.longitude_deg, 6)
                self._state["alt"] = round(pos.relative_altitude_m, 1)
        except asyncio.CancelledError:
            pass

    async def _stream_velocity(self) -> None:
        try:
            async for vel in self._drone.telemetry.velocity_ned():
                speed = math.sqrt(vel.north_m_s ** 2 + vel.east_m_s ** 2)
                self._state["speed"] = round(speed, 1)
        except asyncio.CancelledError:
            pass

    async def _stream_attitude(self) -> None:
        try:
            async for att in self._drone.telemetry.attitude_euler():
                self._state["heading"] = round(att.yaw_deg % 360, 1)
        except asyncio.CancelledError:
            pass

    async def _stream_battery(self) -> None:
        try:
            async for batt in self._drone.telemetry.battery():
                raw = batt.remaining_percent
                if raw < 0:
                    # Negative sentinel — sim has no real battery model
                    self._state["battery"] = "SIM"
                elif raw <= 1.0:
                    # Normal 0.0–1.0 float from MAVSDK
                    self._state["battery"] = round(raw * 100, 1)
                else:
                    # Already a percentage (0–100); cap at 100 for dummy sim values
                    pct = min(raw, 100.0)
                    self._state["battery"] = round(pct, 1)
        except asyncio.CancelledError:
            pass

    async def _stream_flight_mode(self) -> None:
        try:
            async for mode in self._drone.telemetry.flight_mode():
                # Store the raw enum name; the UI layer maps to display text and colour
                self._state["flight_mode"] = mode.name
        except asyncio.CancelledError:
            pass

    async def _stream_armed(self) -> None:
        try:
            async for armed in self._drone.telemetry.armed():
                self._state["armed"] = armed
        except asyncio.CancelledError:
            pass
