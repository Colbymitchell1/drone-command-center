import asyncio
from enum import Enum
from typing import Optional

from PySide6.QtCore import QObject
from mavsdk import System
from mavsdk.offboard import OffboardError, VelocityNedYaw

from app.events.event_bus import bus


class MissionStatus(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    COMPLETE = "complete"
    ABORTED = "aborted"


class LawnmowerExecutor(QObject):
    """
    Executes a lawnmower search pattern via MAVSDK offboard velocity control.

    Call bind() once a drone is connected, unbind() on disconnect.
    start() / abort() are thread-safe — safe to call from the Qt main thread.
    Emits bus.mission_started, bus.mission_completed, bus.mission_aborted.
    """

    # Search pattern parameters
    LEG_SPEED = 2.0    # m/s north/south
    SHIFT_SPEED = 1.0  # m/s east between legs
    LEG_TIME = 6       # seconds per leg
    SHIFT_TIME = 2     # seconds per lateral shift
    NUM_LEGS = 4

    def __init__(self, parent=None):
        super().__init__(parent)
        self._status = MissionStatus.IDLE
        self._drone: Optional[System] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._future: Optional[asyncio.Future] = None

    # ── public API ────────────────────────────────────────────────────────────

    def bind(self, drone: System, loop: asyncio.AbstractEventLoop) -> None:
        self._drone = drone
        self._loop = loop

    def unbind(self) -> None:
        if self._status == MissionStatus.RUNNING:
            self.abort()
        self._drone = None
        self._loop = None

    @property
    def status(self) -> MissionStatus:
        return self._status

    def start(self) -> None:
        if self._status == MissionStatus.RUNNING:
            return
        if not self._drone or not self._loop:
            bus.mission_aborted.emit("No drone connected")
            return
        self._future = asyncio.run_coroutine_threadsafe(self._run(), self._loop)

    def abort(self) -> None:
        if self._future and not self._future.done() and self._loop:
            self._loop.call_soon_threadsafe(self._future.cancel)

    # ── internal helpers ──────────────────────────────────────────────────────

    async def _abort_rtl(self, drone: System) -> None:
        """Stop offboard cleanly and issue RTL, waiting for PX4 mode transition."""
        # Zero velocity so the drone stops moving before we drop offboard
        try:
            await drone.offboard.set_velocity_ned(VelocityNedYaw(0.0, 0.0, 0.0, 0.0))
            await asyncio.sleep(0.5)
        except Exception:
            pass

        # Stop offboard and give PX4 time to complete the mode transition
        try:
            await drone.offboard.stop()
            await asyncio.sleep(1.0)
        except Exception:
            pass

        # Issue RTL with retries in case the first attempt lands in the gap
        for attempt in range(3):
            try:
                await drone.action.return_to_launch()
                return
            except Exception:
                if attempt < 2:
                    await asyncio.sleep(0.5)

    # ── mission coroutine ─────────────────────────────────────────────────────

    async def _run(self) -> None:
        drone = self._drone
        self._status = MissionStatus.RUNNING
        bus.mission_started.emit()

        try:
            await drone.action.arm()
            await drone.action.takeoff()
            await asyncio.sleep(6)

            # Prime offboard with a zero setpoint before starting
            await drone.offboard.set_velocity_ned(VelocityNedYaw(0.0, 0.0, 0.0, 0.0))
            await drone.offboard.start()

            yaw = 0.0
            for i in range(self.NUM_LEGS):
                direction = 1.0 if i % 2 == 0 else -1.0
                await drone.offboard.set_velocity_ned(
                    VelocityNedYaw(direction * self.LEG_SPEED, 0.0, 0.0, yaw)
                )
                await asyncio.sleep(self.LEG_TIME)

                await drone.offboard.set_velocity_ned(
                    VelocityNedYaw(0.0, self.SHIFT_SPEED, 0.0, yaw)
                )
                await asyncio.sleep(self.SHIFT_TIME)

            await drone.offboard.stop()
            await drone.action.hold()
            await asyncio.sleep(3)
            await drone.action.return_to_launch()

            async for in_air in drone.telemetry.in_air():
                if not in_air:
                    break

            self._status = MissionStatus.COMPLETE
            bus.mission_completed.emit()

        except asyncio.CancelledError:
            # Shield the cleanup so a second cancel can't interrupt it mid-sequence.
            try:
                await asyncio.shield(self._abort_rtl(drone))
            except Exception:
                pass
            self._status = MissionStatus.ABORTED
            bus.mission_aborted.emit("Operator abort — RTB")

        except OffboardError as e:
            try:
                await drone.action.return_to_launch()
            except Exception:
                pass
            self._status = MissionStatus.ABORTED
            bus.mission_aborted.emit(f"Offboard error: {e._result.result}")

        except Exception as e:
            self._status = MissionStatus.ABORTED
            bus.mission_aborted.emit(str(e))
