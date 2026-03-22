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
"""

from PySide6.QtCore import QObject

from app.events.event_bus import bus

# ── Thresholds — tune here for different vehicles ─────────────────────────────

WARNING_PCT  = 30.0   # % — emit battery_warning below this level
CRITICAL_PCT = 20.0   # % — emit battery_critical below this level


class BatteryMonitor(QObject):
    """
    Watches battery percentage on every telemetry tick and emits bus signals
    when thresholds are crossed.

    Instantiate once after the dashboard is created.  The monitor is passive —
    it never commands the drone; that is the executor's responsibility.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._warn_active:     bool = False   # True while battery < WARNING_PCT
        self._critical_active: bool = False   # True while battery < CRITICAL_PCT

        bus.telemetry_updated.connect(self._on_telemetry)
        bus.vehicle_disconnected.connect(self._reset)

    # ── slots ─────────────────────────────────────────────────────────────────

    def _on_telemetry(self, data: dict) -> None:
        raw = data.get("battery")

        # Skip simulation sentinel and missing values
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
