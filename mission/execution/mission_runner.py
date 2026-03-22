import asyncio
from typing import Optional

from PySide6.QtCore import QObject
from mavsdk import System

from app.events.event_bus import bus
from mission.execution.executor import MissionStatus


class UploadedMissionRunner(QObject):
    """
    Executes whatever mission is currently uploaded to the drone via
    drone.mission.start_mission().  Monitors progress to detect completion.

    Same bind/unbind/start/abort interface as LawnmowerExecutor.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._status = MissionStatus.IDLE
        self._drone: Optional[System] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._future: Optional[asyncio.Future] = None
        self._abort_reason: Optional[str] = None

        # Hard failsafe — battery_critical always triggers abort regardless of
        # operator input.  Not overridable.
        bus.battery_critical.connect(self._on_battery_critical)

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

    def _on_battery_critical(self, pct: float) -> None:
        if self._status == MissionStatus.RUNNING:
            self._abort_reason = f"Battery critical ({pct:.0f}%) — RTB"
            self.abort()

    # ── mission coroutine ─────────────────────────────────────────────────────

    async def _run(self) -> None:
        drone = self._drone
        self._status = MissionStatus.RUNNING
        bus.mission_started.emit()

        try:
            # PX4 will reject start_mission() if not armed — arm first and
            # give the flight controller a moment to settle before continuing.
            await drone.action.arm()
            await asyncio.sleep(1.0)

            await drone.mission.start_mission()

            # Monitor progress until all items are visited.
            # Emit waypoint_advanced each time progress.current advances so
            # the map can highlight the active target and shade completed segments.
            last_wp = -1
            async for progress in drone.mission.mission_progress():
                if progress.current != last_wp:
                    last_wp = progress.current
                    bus.waypoint_advanced.emit(progress.current)
                if progress.total > 0 and progress.current >= progress.total:
                    break

            # Mission complete — command RTL so the drone returns home and
            # lands instead of hovering indefinitely at the last waypoint.
            for attempt in range(3):
                try:
                    await drone.action.return_to_launch()
                    break
                except Exception:
                    if attempt < 2:
                        await asyncio.sleep(0.5)

            self._status = MissionStatus.COMPLETE
            bus.mission_completed.emit()

        except asyncio.CancelledError:
            try:
                await asyncio.shield(self._abort_rtl(drone))
            except Exception:
                pass
            self._status = MissionStatus.ABORTED
            reason = self._abort_reason or "Operator abort — RTB"
            self._abort_reason = None
            bus.mission_aborted.emit(reason)

        except Exception as e:
            self._status = MissionStatus.ABORTED
            bus.mission_aborted.emit(str(e))

    async def _abort_rtl(self, drone: System) -> None:
        try:
            await drone.mission.pause_mission()
            await asyncio.sleep(0.5)
        except Exception:
            pass
        for attempt in range(3):
            try:
                await drone.action.return_to_launch()
                return
            except Exception:
                if attempt < 2:
                    await asyncio.sleep(0.5)
