"""
In-flight battery monitor.

Subscribes to bus.telemetry_updated and emits bus.battery_warning /
bus.battery_critical the first time battery percentage crosses each
threshold.  Hysteresis prevents repeated alerts: a threshold won't
fire again until battery rises back above it (which can happen in
simulation when the vehicle resets, or during battery hot-swap on
real hardware).

Constants at the top of this file are the only place thresholds
need to be changed.

──────────────────────────────────────────────────────────────────────────────
SIM MODE SUPPRESSION
──────────────────────────────────────────────────────────────────────────────
Battery thresholds are production safety features designed for real hardware.
PX4 SITL does not model battery discharge accurately — it may report a fixed
value, a slowly drifting value, or -1 depending on the world and plugin
configuration.  Acting on those readings in simulation would cause spurious
battery_warning / battery_critical events that abort simulated missions and
train the operator to ignore real alerts.

For these reasons, all threshold checks are suppressed when the mode is SIM.
The monitor stays subscribed and can be switched to enforcement at any time
by changing the mode to REAL — no restart required.
──────────────────────────────────────────────────────────────────────────────
"""

from PySide6.QtCore import QObject

from app.events.event_bus import bus
from app.state.state_store import DroneMode, StateStore

# ── Thresholds — tune here for different vehicles ─────────────────────────────

WARNING_PCT  = 30.0   # % — emit battery_warning below this level
CRITICAL_PCT = 20.0   # % — emit battery_critical below this level


class BatteryMonitor(QObject):
    """
    Watches battery percentage on every telemetry tick and emits bus signals
    when thresholds are crossed.

    In SIM mode all checks are silently skipped (see module docstring).
    In REAL mode full threshold enforcement with hysteresis applies.

    Instantiate once after the dashboard is created.  The monitor is passive —
    it never commands the drone; that is the executor's responsibility.
    """

    def __init__(self, state: StateStore, parent=None) -> None:
        super().__init__(parent)
        self._state = state
        self._warn_active:     bool = False   # True while battery < WARNING_PCT
        self._critical_active: bool = False   # True while battery < CRITICAL_PCT

        bus.telemetry_updated.connect(self._on_telemetry)
        bus.vehicle_disconnected.connect(self._reset)

    # ── slots ─────────────────────────────────────────────────────────────────

    def _on_telemetry(self, data: dict) -> None:
        # Suppress entirely in SIM mode — simulator battery readings are not
        # reliable enough to drive safety-critical alerts.
        if self._state.mode == DroneMode.SIM:
            return

        raw = data.get("battery")

        # Skip missing values and the SIM sentinel string
        if raw is None or not isinstance(raw, (int, float)):
            return

        pct = float(raw)

        # ── critical threshold ─────────────────────────────────────────────
        if pct < CRITICAL_PCT:
            if not self._critical_active:
                self._critical_active = True
                bus.battery_critical.emit(pct)
        else:
            self._critical_active = False

        # ── warning threshold ──────────────────────────────────────────────
        if pct < WARNING_PCT:
            if not self._warn_active:
                self._warn_active = True
                bus.battery_warning.emit(pct)
        else:
            self._warn_active = False

    def _reset(self) -> None:
        """Clear hysteresis state on disconnect so next connection starts clean."""
        self._warn_active     = False
        self._critical_active = False
