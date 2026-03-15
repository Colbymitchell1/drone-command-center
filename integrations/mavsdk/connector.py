import asyncio
import threading
from typing import Optional

from PySide6.QtCore import QObject
from mavsdk import System

from app.events.event_bus import bus
from integrations.mavsdk.telemetry import TelemetryManager


class DroneConnector(QObject):
    """
    Owns the MAVSDK System instance and the background asyncio event loop thread.

    Usage:
        connector.connect(port=14540)   # non-blocking, emits bus.vehicle_connected on success
        connector.disconnect()          # non-blocking, emits bus.vehicle_disconnected
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loop = asyncio.new_event_loop()
        self._drone: Optional[System] = None
        self._telemetry: Optional[TelemetryManager] = None

        t = threading.Thread(target=self._run_loop, daemon=True, name="mavsdk-loop")
        t.start()

    # ── public API ────────────────────────────────────────────────────────────

    def connect(self, port: int = 14540) -> None:
        asyncio.run_coroutine_threadsafe(self._connect(port), self._loop)

    def disconnect(self) -> None:
        asyncio.run_coroutine_threadsafe(self._disconnect(), self._loop)

    # ── internals ─────────────────────────────────────────────────────────────

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    async def _connect(self, port: int) -> None:
        try:
            addr = f"udpin://0.0.0.0:{port}"
            self._drone = System()
            await self._drone.connect(system_address=addr)

            async for state in self._drone.core.connection_state():
                if state.is_connected:
                    bus.vehicle_connected.emit()
                    self._telemetry = TelemetryManager(self._drone, self._loop)
                    self._telemetry.start()
                    break
        except Exception as e:
            bus.vehicle_error.emit(str(e))

    async def _disconnect(self) -> None:
        if self._telemetry:
            self._telemetry.stop()
            self._telemetry = None
        self._drone = None
        bus.vehicle_disconnected.emit()
