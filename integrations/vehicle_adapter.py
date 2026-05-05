"""
integrations/vehicle_adapter.py

Abstract VehicleAdapter interface.

Every vehicle integration (MAVLink, DJI, future protocols) implements this
contract. The rest of the application -- mission engine, UI, MQTT publisher,
LLM layer -- never imports MAVSDK or any other transport type directly.

Implementing a new adapter:
    1. Subclass VehicleAdapter
    2. Implement every abstract method
    3. Return CommandResult.unsupported() for capabilities your adapter lacks
    4. Never let transport-specific types leak past this boundary
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Callable, Optional

from app.models.domain import (
    CommandResult,
    FlightLog,
    MissionPlan,
    MissionValidationResult,
    VehicleCapabilities,
    VehicleHealth,
    VehicleIdentity,
    VehicleState,
)


# ---------------------------------------------------------------------------
# Subscription handle
# ---------------------------------------------------------------------------


class Subscription:
    """
    Handle returned by subscribe_* methods.
    Call cancel() to stop receiving updates.
    """

    def __init__(self, cancel_fn: Callable[[], None]):
        self._cancel = cancel_fn
        self._active = True

    def cancel(self) -> None:
        if self._active:
            self._cancel()
            self._active = False

    @property
    def active(self) -> bool:
        return self._active


# ---------------------------------------------------------------------------
# VehicleAdapter
# ---------------------------------------------------------------------------


class VehicleAdapter(ABC):
    """
    Abstract base class for all vehicle integrations.

    Design rules:
    - All command methods are async and return CommandResult -- never raise.
    - All state is delivered via subscriptions, not polling.
    - No transport types (MAVSDK, MAVLink, etc.) appear in any signature.
    - Unsupported methods return CommandResult.unsupported(), never raise.
    """

    # ── Identity and capabilities ────────────────────────────────────────────

    @abstractmethod
    async def get_identity(self) -> VehicleIdentity:
        """Return static identity information about this vehicle."""
        ...

    @abstractmethod
    async def get_capabilities(self) -> VehicleCapabilities:
        """
        Return feature flags for what this adapter supports.
        Call this before invoking optional methods to avoid unsupported errors.
        """
        ...

    # ── Connection lifecycle ─────────────────────────────────────────────────

    @abstractmethod
    async def connect(self, connection_string: str) -> CommandResult:
        """
        Connect to the vehicle.

        connection_string format is adapter-specific:
            MAVLink:  "udpin://0.0.0.0:14540"
            Serial:   "/dev/ttyUSB0:57600"
        """
        ...

    @abstractmethod
    async def disconnect(self) -> CommandResult:
        """Cleanly disconnect. Stop all telemetry streams."""
        ...

    @abstractmethod
    def is_connected(self) -> bool:
        """Return current connection status synchronously."""
        ...

    # ── State and health ─────────────────────────────────────────────────────

    @abstractmethod
    async def get_state(self) -> VehicleState:
        """Return the latest telemetry snapshot."""
        ...

    @abstractmethod
    async def get_health(self) -> VehicleHealth:
        """
        Return a structured health/readiness report.
        This is the source of truth for the preflight check flow.
        """
        ...

    # ── Subscriptions ────────────────────────────────────────────────────────

    @abstractmethod
    def subscribe_state(
        self,
        callback: Callable[[VehicleState], None],
        hz: float = 4.0,
    ) -> Subscription:
        """
        Subscribe to telemetry state updates at the requested rate.
        The callback is called from the adapter's internal loop --
        implementations must ensure thread safety when bridging to Qt.
        """
        ...

    # ── Mission lifecycle ────────────────────────────────────────────────────

    @abstractmethod
    async def validate_mission(self, plan: MissionPlan) -> MissionValidationResult:
        """
        Validate a mission plan without touching the vehicle.
        Run this before upload to catch geometry and parameter errors early.
        """
        ...

    @abstractmethod
    async def upload_mission(self, plan: MissionPlan) -> CommandResult:
        """Upload a validated mission plan to the vehicle."""
        ...

    @abstractmethod
    async def start_mission(self) -> CommandResult:
        """Arm and begin executing the uploaded mission."""
        ...

    @abstractmethod
    async def pause_mission(self) -> CommandResult:
        """Pause mission execution. Vehicle holds position."""
        ...

    @abstractmethod
    async def resume_mission(self) -> CommandResult:
        """Resume a paused mission from the current waypoint."""
        ...

    @abstractmethod
    async def cancel_mission(self) -> CommandResult:
        """Cancel mission execution. Does not trigger RTL."""
        ...

    # ── Safety commands ──────────────────────────────────────────────────────

    @abstractmethod
    async def return_to_launch(self) -> CommandResult:
        """Command RTL. This should always be implemented."""
        ...

    @abstractmethod
    async def land(self) -> CommandResult:
        """Command immediate landing at current position."""
        ...

    async def goto_location(
        self,
        lat: float,
        lon: float,
        alt_m: float,
    ) -> CommandResult:
        """
        Fly to a specific location. Optional -- check supports_guided_goto first.
        Default implementation returns unsupported.
        """
        return CommandResult.unsupported("goto_location")

    # ── Logging ──────────────────────────────────────────────────────────────

    async def export_vehicle_log(self) -> Optional[bytes]:
        """
        Export the vehicle's internal flight log (e.g. PX4 .ulg file).
        Optional -- check supports_log_download first.
        Default implementation returns None.
        """
        return None

    # ── Context manager support ──────────────────────────────────────────────

    async def __aenter__(self) -> "VehicleAdapter":
        return self

    async def __aexit__(self, *_) -> None:
        await self.disconnect()
