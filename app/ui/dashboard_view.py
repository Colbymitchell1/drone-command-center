"""DashboardView — unified mission control layout.

Layout: horizontal QSplitter
    • Left  (320 px fixed) — QScrollArea containing all controls
    • Right (remaining)    — CesiumJS MapView
"""

import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal, Slot
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.events.event_bus import bus
from app.services.battery_monitor import BatteryMonitor
from app.services.sim_controller import SimController
from app.services.sim_controller import SIM_HOME_LAT, SIM_HOME_LON
from app.state.state_store import DroneMode, StateStore
from app.ui.map_view import MapView
from integrations.mavsdk.connector import DroneConnector
from mission.execution.executor import LawnmowerExecutor, MissionStatus
from mission.execution.mission_runner import UploadedMissionRunner
from mission.planning.lawnmower import generate_lawnmower, offsets_to_latlon, polygon_center
from mission.validation.preflight import PreflightChecker


# ── colour constants (new dark palette) ───────────────────────────────────────

_C_BG        = "#0d1117"
_C_PANEL     = "#161b22"
_C_BORDER    = "#30363d"
_C_TEXT      = "#e6edf3"
_C_MUTED     = "#8b949e"
_C_ACCENT    = "#1f6feb"
_C_SUCCESS   = "#238636"
_C_DANGER    = "#da3633"
_C_WARNING   = "#d29922"
_C_CYAN      = "#00d4ff"

_BADGE_BASE = (
    "font-weight: bold; font-size: 10px; border-radius: 6px; padding: 2px 8px;"
)


# ── shared status helpers ──────────────────────────────────────────────────────

def _make_dot_label() -> QLabel:
    lbl = QLabel("●")
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lbl.setFixedWidth(18)
    _apply_dot(lbl, None)
    return lbl


def _apply_dot(lbl: QLabel, ok: bool | None) -> None:
    if ok is None:
        color = "#484f58"
    elif ok:
        color = _C_SUCCESS
    else:
        color = _C_DANGER
    lbl.setText("●")
    lbl.setStyleSheet(
        f"color: {color}; font-size: 14px; background: transparent; border: none;"
    )


# ── TelemetryPanel ─────────────────────────────────────────────────────────────

# Maps raw FlightMode enum name → operator-facing display string
_MODE_DISPLAY: dict[str, str] = {
    "RETURN_TO_LAUNCH": "RTL",
    "UNKNOWN":          "---",
}

# Maps raw FlightMode enum name → (text colour, background colour)
_MODE_COLORS: dict[str, tuple[str, str]] = {
    "MISSION":          (_C_ACCENT,   "#0d2a40"),
    "HOLD":             (_C_WARNING,  "#3a2d00"),
    "RETURN_TO_LAUNCH": ("#ff9800",   "#3a1e00"),
    "LAND":             ("#ff9800",   "#3a1e00"),
    "OFFBOARD":         ("#ce93d8",   "#2d0f40"),
    "TAKEOFF":          (_C_SUCCESS,  "#1b3a1f"),
}
_MODE_COLOR_DEFAULT = (_C_MUTED, _C_PANEL)


class TelemetryPanel(QGroupBox):
    """
    Compact 3×2 telemetry grid + armed/mode badges.
    Fits in the 320 px left panel.
    """
    _FIELDS = [
        ("lat",     "LAT",     "°"),
        ("lon",     "LON",     "°"),
        ("alt",     "ALT",     " m"),
        ("speed",   "SPEED",   " m/s"),
        ("heading", "HEADING", "°"),
        ("battery", "BATTERY", "%"),
    ]

    def __init__(self, parent=None):
        super().__init__("Telemetry", parent)
        self._value_labels: dict[str, QLabel] = {}
        self._build_ui()
        bus.telemetry_updated.connect(self._on_telemetry)

    def _build_ui(self) -> None:
        from PySide6.QtWidgets import QGridLayout
        root = QVBoxLayout(self)
        root.setSpacing(6)
        root.setContentsMargins(8, 10, 8, 8)

        grid = QGridLayout()
        grid.setSpacing(4)
        grid.setHorizontalSpacing(8)

        for idx, (key, display_name, unit) in enumerate(self._FIELDS):
            row, col = divmod(idx, 3)

            name_lbl = QLabel(display_name)
            name_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            name_lbl.setStyleSheet(
                f"color: {_C_MUTED}; font-size: 9px; letter-spacing: 1px;"
                "background: transparent;"
            )

            val_lbl = QLabel("---")
            val_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            val_lbl.setStyleSheet(
                f"font-size: 14px; font-weight: bold; color: {_C_TEXT};"
                "background: transparent;"
            )
            val_lbl.setProperty("unit", unit)
            self._value_labels[key] = val_lbl

            cell = QVBoxLayout()
            cell.setSpacing(1)
            cell.addWidget(name_lbl)
            cell.addWidget(val_lbl)

            cell_w = QWidget()
            cell_w.setLayout(cell)
            cell_w.setStyleSheet(
                f"background: {_C_BG}; border-radius: 4px; padding: 4px;"
            )
            grid.addWidget(cell_w, row, col)

        root.addLayout(grid)

        # ── Badges row ─────────────────────────────────────────────────────
        badges = QHBoxLayout()
        badges.setSpacing(6)

        self._armed_badge = QLabel("DISARMED")
        self._armed_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._armed_badge.setFixedWidth(76)
        self._armed_badge.setStyleSheet(
            f"color: {_C_MUTED}; background: {_C_PANEL}; {_BADGE_BASE}"
        )
        badges.addWidget(self._armed_badge)

        self._mode_badge = QLabel("---")
        self._mode_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._mode_badge.setFixedWidth(76)
        self._mode_badge.setStyleSheet(
            f"color: {_C_MUTED}; background: {_C_PANEL}; {_BADGE_BASE}"
        )
        badges.addWidget(self._mode_badge)
        badges.addStretch()

        root.addLayout(badges)

    def _on_telemetry(self, data: dict) -> None:
        for key, lbl in self._value_labels.items():
            if key in data:
                unit = lbl.property("unit") or ""
                raw = data[key]
                if isinstance(raw, float):
                    text = f"{raw:.4f}" if key in ("lat", "lon") else f"{raw:.1f}"
                    lbl.setText(f"{text}{unit}")
                else:
                    lbl.setText(str(raw))

        if "armed" in data and data["armed"] is not None:
            self._update_armed_badge(data["armed"])
        if "flight_mode" in data and data["flight_mode"] is not None:
            self._update_mode_badge(data["flight_mode"])

    def _update_armed_badge(self, armed: bool) -> None:
        if armed:
            self._armed_badge.setText("ARMED")
            self._armed_badge.setStyleSheet(
                f"color: {_C_SUCCESS}; background: #1b3a1f; {_BADGE_BASE}"
            )
        else:
            self._armed_badge.setText("DISARMED")
            self._armed_badge.setStyleSheet(
                f"color: {_C_MUTED}; background: {_C_PANEL}; {_BADGE_BASE}"
            )

    def _update_mode_badge(self, raw_mode: str) -> None:
        display = _MODE_DISPLAY.get(raw_mode, raw_mode)
        text_color, bg_color = _MODE_COLORS.get(raw_mode, _MODE_COLOR_DEFAULT)
        self._mode_badge.setText(display)
        self._mode_badge.setStyleSheet(
            f"color: {text_color}; background: {bg_color}; {_BADGE_BASE}"
        )


# ── SystemHealthPanel ──────────────────────────────────────────────────────────

class SystemHealthPanel(QGroupBox):
    _ROWS = [
        ("px4",       "PX4 SITL"),
        ("gazebo",    "Gazebo"),
        ("udp_14540", "UDP 14540"),
        ("udp_14550", "UDP 14550"),
    ]

    def __init__(self, sim_controller: SimController, parent=None):
        super().__init__("System", parent)
        self._sim = sim_controller
        self._indicators: dict[str, QLabel] = {}
        self._build_ui()
        self._sim.health_changed.connect(self._apply_health)
        QTimer.singleShot(0, lambda: self._apply_health(self._sim.get_health()))

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(4)
        layout.setContentsMargins(8, 10, 8, 8)

        for key, label_text in self._ROWS:
            row = QHBoxLayout()
            row.setSpacing(6)

            dot = _make_dot_label()
            self._indicators[key] = dot

            name = QLabel(label_text)
            name.setStyleSheet(
                f"color: {_C_MUTED}; font-size: 11px; background: transparent;"
            )

            row.addWidget(dot)
            row.addWidget(name)
            row.addStretch()
            layout.addLayout(row)

    def _apply_health(self, health: dict) -> None:
        for key, lbl in self._indicators.items():
            if key in health:
                _apply_dot(lbl, health[key])


# ── ConnectPanel ───────────────────────────────────────────────────────────────

class ConnectPanel(QWidget):
    """UDP port input + Connect/Disconnect button + live status."""

    def __init__(self, connector: DroneConnector, parent=None):
        super().__init__(parent)
        self._connector = connector
        self._build_ui()
        bus.vehicle_connected.connect(self._on_connected)
        bus.vehicle_disconnected.connect(self._on_disconnected)
        bus.vehicle_error.connect(self._on_error)

    def _build_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        udp_lbl = QLabel("UDP:")
        udp_lbl.setStyleSheet(f"color: {_C_MUTED}; font-size: 11px;")
        layout.addWidget(udp_lbl)

        self._port_input = QLineEdit("14540")
        self._port_input.setFixedWidth(56)
        layout.addWidget(self._port_input)

        self._connect_btn = QPushButton("Connect")
        self._connect_btn.setFixedWidth(88)
        self._connect_btn.clicked.connect(self._on_btn_clicked)
        layout.addWidget(self._connect_btn)

        self._status_lbl = QLabel("Disconnected")
        self._status_lbl.setStyleSheet(f"color: {_C_MUTED}; font-size: 11px;")
        layout.addWidget(self._status_lbl)
        layout.addStretch()

    def _on_btn_clicked(self) -> None:
        if self._connect_btn.text() == "Disconnect":
            self._connector.disconnect()
        else:
            try:
                port = int(self._port_input.text())
            except ValueError:
                self._status_lbl.setText("Invalid port")
                return
            self._connect_btn.setEnabled(False)
            self._connect_btn.setText("Connecting…")
            self._status_lbl.setText("Connecting…")
            self._connector.connect(port)

    def _on_connected(self) -> None:
        self._connect_btn.setEnabled(True)
        self._connect_btn.setText("Disconnect")
        self._port_input.setEnabled(False)
        self._status_lbl.setText("Connected")
        self._status_lbl.setStyleSheet(
            f"color: {_C_SUCCESS}; font-weight: bold; font-size: 11px;"
        )

    def _on_disconnected(self) -> None:
        self._connect_btn.setEnabled(True)
        self._connect_btn.setText("Connect")
        self._port_input.setEnabled(True)
        self._status_lbl.setText("Disconnected")
        self._status_lbl.setStyleSheet(f"color: {_C_MUTED}; font-size: 11px;")

    def _on_error(self, msg: str) -> None:
        self._connect_btn.setEnabled(True)
        self._connect_btn.setText("Connect")
        self._port_input.setEnabled(True)
        self._status_lbl.setText(f"Error: {msg}")
        self._status_lbl.setStyleSheet(f"color: {_C_DANGER}; font-size: 11px;")


# ── BatteryBanner ──────────────────────────────────────────────────────────────

class BatteryBanner(QWidget):
    """Single-line alert bar; hidden when battery is normal or vehicle disconnected."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 5, 10, 5)
        layout.setSpacing(8)

        self._icon = QLabel("⚠")
        self._text = QLabel()
        self._text.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self._icon)
        layout.addWidget(self._text, stretch=1)

        bus.battery_warning.connect(self._on_warning)
        bus.battery_critical.connect(self._on_critical)
        bus.vehicle_disconnected.connect(self.hide)
        self.hide()

    def _on_warning(self, pct: float) -> None:
        self._icon.setStyleSheet(f"font-size: 14px; color: {_C_WARNING};")
        self._text.setStyleSheet(f"font-size: 12px; font-weight: bold; color: {_C_WARNING};")
        self._text.setText(f"Low battery ({pct:.0f}%) — consider aborting")
        self.setStyleSheet(f"background: #3a2d00; border-radius: 4px;")
        self.show()

    def _on_critical(self, pct: float) -> None:
        self._icon.setStyleSheet(f"font-size: 14px; color: {_C_DANGER};")
        self._text.setStyleSheet(f"font-size: 12px; font-weight: bold; color: {_C_DANGER};")
        self._text.setText(f"Critical battery ({pct:.0f}%) — returning to home")
        self.setStyleSheet(f"background: #3a1010; border-radius: 4px;")
        self.show()


# ── MissionPanel ───────────────────────────────────────────────────────────────

_STATUS_STYLES = {
    MissionStatus.IDLE:     ("IDLE",     _C_MUTED),
    MissionStatus.RUNNING:  ("RUNNING",  "#ff9800"),
    MissionStatus.COMPLETE: ("COMPLETE", _C_SUCCESS),
    MissionStatus.ABORTED:  ("ABORTED",  _C_DANGER),
}


class MissionPanel(QGroupBox):
    _preflight_done: Signal = Signal(object)

    def __init__(
        self,
        executor: LawnmowerExecutor,
        runner: UploadedMissionRunner,
        connector: DroneConnector,
        planner,          # duck-typed: .offsets, .spacing_m
        parent=None,
    ):
        super().__init__("Mission", parent)
        self._executor  = executor
        self._runner    = runner
        self._connector = connector
        self._planner   = planner
        self._checker   = PreflightChecker()
        self._active    = None
        self._pending   = None
        self._mission_uploaded = False

        self._build_ui()

        bus.vehicle_connected.connect(self._on_connected)
        bus.vehicle_disconnected.connect(self._on_disconnected)
        bus.mission_started.connect(lambda: self._set_status(MissionStatus.RUNNING))
        bus.mission_completed.connect(lambda: self._set_status(MissionStatus.COMPLETE))
        bus.mission_aborted.connect(lambda _: self._set_status(MissionStatus.ABORTED))
        bus.mission_uploaded.connect(self._on_mission_uploaded)
        self._preflight_done.connect(self._on_preflight_done)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(6)
        root.setContentsMargins(8, 10, 8, 8)

        # Status row
        status_row = QHBoxLayout()
        lbl = QLabel("STATUS:")
        lbl.setStyleSheet(f"color: {_C_MUTED}; font-size: 10px; letter-spacing: 1px;")
        status_row.addWidget(lbl)

        self._status_lbl = QLabel("IDLE")
        self._status_lbl.setFixedWidth(78)
        self._status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_lbl.setStyleSheet(
            f"color: {_C_MUTED}; font-weight: bold; font-size: 11px;"
            f"background: {_C_PANEL}; border-radius: 4px; padding: 2px 6px;"
        )
        status_row.addWidget(self._status_lbl)
        status_row.addStretch()

        self._abort_btn = QPushButton("Abort")
        self._abort_btn.setFixedWidth(68)
        self._abort_btn.setEnabled(False)
        self._abort_btn.setStyleSheet(
            f"color: {_C_DANGER}; border-color: {_C_DANGER};"
        )
        self._abort_btn.clicked.connect(self._on_abort)
        status_row.addWidget(self._abort_btn)

        # Button rows
        self._lawnmower_btn = QPushButton("Start Lawnmower Search")
        self._lawnmower_btn.setEnabled(False)
        self._lawnmower_btn.setToolTip("Offboard velocity lawnmower — no upload required")
        self._lawnmower_btn.clicked.connect(self._on_start_lawnmower)

        self._mission_btn = QPushButton("Start Uploaded Mission")
        self._mission_btn.setEnabled(False)
        self._mission_btn.setToolTip("Execute the mission currently uploaded to the drone")
        self._mission_btn.clicked.connect(self._on_start_mission)

        root.addLayout(status_row)
        root.addWidget(self._lawnmower_btn)
        root.addWidget(self._mission_btn)

    def _set_status(self, status: MissionStatus) -> None:
        text, color = _STATUS_STYLES[status]
        self._status_lbl.setText(text)
        self._status_lbl.setStyleSheet(
            f"color: {color}; font-weight: bold; font-size: 11px;"
            f"background: {_C_PANEL}; border-radius: 4px; padding: 2px 6px;"
        )
        running = status == MissionStatus.RUNNING
        self._lawnmower_btn.setEnabled(not running)
        self._mission_btn.setEnabled(not running and self._mission_uploaded)
        self._abort_btn.setEnabled(running)

    def _on_connected(self) -> None:
        self._lawnmower_btn.setEnabled(True)

    def _on_disconnected(self) -> None:
        self._lawnmower_btn.setEnabled(False)
        self._mission_btn.setEnabled(False)
        self._abort_btn.setEnabled(False)
        self._active  = None
        self._pending = None
        self._set_status(MissionStatus.IDLE)

    def _on_mission_uploaded(self) -> None:
        self._mission_uploaded = True
        if self._lawnmower_btn.isEnabled():
            self._mission_btn.setEnabled(True)

    def _on_start_lawnmower(self) -> None:
        self._pending = "lawnmower"
        offsets   = LawnmowerExecutor.get_path_offsets()
        spacing_m = float(LawnmowerExecutor.SHIFT_SPEED * LawnmowerExecutor.SHIFT_TIME)
        self._run_preflight(offsets, spacing_m)

    def _on_start_mission(self) -> None:
        self._pending = "mission"
        offsets   = self._planner.offsets
        spacing_m = self._planner.spacing_m
        self._run_preflight(offsets, spacing_m)

    def _on_abort(self) -> None:
        if self._active:
            self._active.abort()

    def _run_preflight(self, offsets: list, spacing_m: float) -> None:
        if not self._connector.drone or not self._connector.loop:
            bus.mission_aborted.emit("No drone connected")
            self._pending = None
            return

        self._lawnmower_btn.setEnabled(False)
        self._mission_btn.setEnabled(False)
        self._pending_offsets = offsets
        self._pending_spacing = spacing_m

        future = asyncio.run_coroutine_threadsafe(
            self._checker.run_checks(
                self._connector.drone, offsets, spacing_m
            ),
            self._connector.loop,
        )
        future.add_done_callback(self._on_preflight_future_done)

    def _on_preflight_future_done(self, future) -> None:
        try:
            result = future.result()
        except Exception:
            result = None
        self._preflight_done.emit(result)

    def _on_preflight_done(self, result) -> None:
        if result is None:
            self._restore_start_buttons()
            self._pending = None
            return

        from app.ui.preflight_dialog import PreflightDialog

        dlg = PreflightDialog(
            result,
            self._connector.drone,
            self._pending_offsets,
            self._pending_spacing,
            self._checker,
            self._connector.loop,
            parent=self,
        )

        if dlg.exec() == QDialog.DialogCode.Accepted:
            if self._pending == "lawnmower":
                self._active = self._executor
                self._executor.start()
            elif self._pending == "mission":
                self._active = self._runner
                self._runner.start()
        else:
            self._restore_start_buttons()

        self._pending = None

    def _restore_start_buttons(self) -> None:
        self._lawnmower_btn.setEnabled(True)
        self._mission_btn.setEnabled(self._mission_uploaded)


# ── Report path ────────────────────────────────────────────────────────────────

_REPORTS_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "reports"


# ── Geometry helpers ───────────────────────────────────────────────────────────

def _convex_hull(points: list) -> list:
    """Return convex hull of [[lat,lon],...] in CCW order (Andrew's monotone chain)."""
    pts = sorted(set((p[1], p[0]) for p in points))  # dedupe, sort by (lon, lat)
    if len(pts) < 3:
        return points

    def cross(O, A, B):
        return (A[0] - O[0]) * (B[1] - O[1]) - (A[1] - O[1]) * (B[0] - O[0])

    lower = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)

    upper = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)

    hull = lower[:-1] + upper[:-1]
    return [[p[1], p[0]] for p in hull]  # back to [lat, lon]


# ── DashboardView ──────────────────────────────────────────────────────────────

class _PlannerProxy:
    """Duck-typed interface so MissionPanel can get offsets and spacing."""

    def __init__(self, get_offsets, get_spacing):
        self._get_offsets  = get_offsets
        self._get_spacing  = get_spacing

    @property
    def offsets(self) -> list:
        return self._get_offsets()

    @property
    def spacing_m(self) -> float:
        return self._get_spacing()


class DashboardView(QWidget):
    _upload_result = Signal(bool, str)
    _ai_result     = Signal(object)
    _report_ready  = Signal(str)

    def __init__(
        self,
        state: StateStore,
        sim_controller: SimController,
        connector: DroneConnector,
        ai_service=None,
        parent=None,
    ):
        super().__init__(parent)
        self._state      = state
        self._sim        = sim_controller
        self._connector  = connector
        self._ai_service = ai_service

        self._executor        = LawnmowerExecutor(self)
        self._runner          = UploadedMissionRunner(self)
        self._battery_monitor = BatteryMonitor(self._state, self)

        # Mission planning state
        self._polygon:      list                   = []
        self._offsets:      list                   = []
        self._last_drone_pos: Optional[tuple]      = None
        self._last_telemetry: dict                 = {}
        self._mission_start_time: Optional[float]  = None

        self._build_ui()
        self._wire_executors()
        self._wire_bus()

    # ── executor bindings ─────────────────────────────────────────────────────

    def _wire_executors(self) -> None:
        bus.vehicle_connected.connect(
            lambda: self._executor.bind(self._connector.drone, self._connector.loop)
        )
        bus.vehicle_connected.connect(
            lambda: self._runner.bind(self._connector.drone, self._connector.loop)
        )
        bus.vehicle_disconnected.connect(self._executor.unbind)
        bus.vehicle_disconnected.connect(self._runner.unbind)

    # ── bus subscriptions ─────────────────────────────────────────────────────

    def _wire_bus(self) -> None:
        bus.sim_started.connect(self._on_sim_ready)
        bus.telemetry_updated.connect(self._on_telemetry_snapshot)
        bus.telemetry_updated.connect(self._on_telemetry_map)
        bus.vehicle_disconnected.connect(self._on_vehicle_disconnected)

        bus.mission_started.connect(self._on_mission_started)
        bus.mission_completed.connect(self._on_mission_completed)
        bus.mission_aborted.connect(lambda _: self._map.clear_mission_state())
        bus.mission_waypoints_ready.connect(self._on_waypoints_ready)
        bus.waypoint_advanced.connect(self._on_waypoint_advanced)

        self._upload_result.connect(self._on_upload_result)
        self._ai_result.connect(self._on_ai_result)
        self._report_ready.connect(self._on_report_ready)

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(1)
        splitter.setStyleSheet("QSplitter::handle { background: #30363d; }")

        # Left panel — 320px fixed
        left_scroll = self._build_left_panel()
        splitter.addWidget(left_scroll)

        # Right panel — map
        self._map = MapView()
        self._map.polygon_received.connect(self._on_polygon)
        splitter.addWidget(self._map)

        splitter.setSizes([320, 9999])
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        # Prevent left panel from being resized smaller than 280px
        left_scroll.setMinimumWidth(280)
        left_scroll.setMaximumWidth(360)

        root.addWidget(splitter)

    def _build_left_panel(self) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setFixedWidth(320)
        scroll.setStyleSheet(
            f"QScrollArea {{ background: {_C_PANEL}; border: none; border-right: 1px solid {_C_BORDER}; }}"
        )

        inner = QWidget()
        inner.setStyleSheet(f"background: {_C_PANEL};")
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # 1. Header (mode badge + world selector + sim button + UDP connect)
        layout.addWidget(self._build_header())

        # 2. Telemetry grid
        layout.addWidget(TelemetryPanel())

        # 3. System health
        layout.addWidget(SystemHealthPanel(self._sim))

        # 4. Mission section
        proxy = _PlannerProxy(
            lambda: self._offsets,
            lambda: float(self._spacing_spin.value()),
        )
        self._mission_panel = MissionPanel(
            self._executor, self._runner, self._connector, proxy
        )
        layout.addWidget(self._mission_panel)

        # 5. Battery banner (hidden by default)
        layout.addWidget(BatteryBanner())

        # 6. AI Mission Assist
        layout.addWidget(self._build_ai_section())

        # 7. Mission planning
        layout.addWidget(self._build_planning_section())

        # 8. Mission report (hidden until generated)
        self._report_group = self._build_report_section()
        layout.addWidget(self._report_group)

        layout.addStretch()

        scroll.setWidget(inner)
        return scroll

    # ── header ────────────────────────────────────────────────────────────────

    def _build_header(self) -> QWidget:
        w = QWidget()
        w.setStyleSheet(f"background: transparent;")
        vbox = QVBoxLayout(w)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(6)

        # Row 1: MODE badge + world selector + Start Sim + map toggle
        top = QHBoxLayout()
        top.setSpacing(6)

        mode_badge = QLabel(f"MODE: {self._state.mode.value}")
        mode_badge.setStyleSheet(
            f"font-weight: bold; font-size: 12px; color: {_C_CYAN};"
            "letter-spacing: 1px; background: transparent;"
        )
        top.addWidget(mode_badge)

        self._world_combo = QComboBox()
        self._world_combo.addItems(["baylands", "lawn", "windy", "forest", "default"])
        self._world_combo.setFixedWidth(90)
        self._world_combo.setVisible(self._state.mode == DroneMode.SIM)
        top.addWidget(self._world_combo)

        self._start_btn = QPushButton("Start Sim")
        self._start_btn.setFixedWidth(90)
        self._start_btn.clicked.connect(self._on_start_sim)
        self._start_btn.setVisible(self._state.mode == DroneMode.SIM)
        top.addWidget(self._start_btn)

        top.addStretch()

        # Map style toggle button (🌙 / 🛰)
        self._map_toggle_btn = QPushButton("🌙")
        self._map_toggle_btn.setFixedSize(30, 28)
        self._map_toggle_btn.setToolTip("Toggle map style: Dark Tactical / Satellite")
        self._map_toggle_btn.setStyleSheet(
            f"background: {_C_BG}; border: 1px solid {_C_BORDER};"
            "border-radius: 4px; font-size: 14px; padding: 0;"
        )
        self._map_toggle_btn.clicked.connect(self._on_map_toggle)
        top.addWidget(self._map_toggle_btn)

        vbox.addLayout(top)

        # Row 2: UDP connect
        vbox.addWidget(ConnectPanel(self._connector))

        return w

    # ── AI section ────────────────────────────────────────────────────────────

    def _build_ai_section(self) -> QGroupBox:
        box = QGroupBox("AI Mission Assist")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(8, 10, 8, 8)
        layout.setSpacing(6)

        input_row = QHBoxLayout()
        self._ai_input = QLineEdit()
        self._ai_input.setPlaceholderText("Describe your search mission…")
        self._ai_input.returnPressed.connect(self._on_generate_mission)
        input_row.addWidget(self._ai_input, stretch=1)

        self._ai_btn = QPushButton("Generate")
        self._ai_btn.setFixedWidth(78)
        self._ai_btn.clicked.connect(self._on_generate_mission)
        input_row.addWidget(self._ai_btn)

        self._ai_status = QLabel()
        self._ai_status.setStyleSheet(f"color: {_C_MUTED}; font-size: 11px;")
        self._ai_status.setWordWrap(True)

        layout.addLayout(input_row)
        layout.addWidget(self._ai_status)

        self._refresh_ai_panel()
        return box

    # ── planning section ──────────────────────────────────────────────────────

    def _build_planning_section(self) -> QGroupBox:
        box = QGroupBox("Mission Planning")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(8, 10, 8, 8)
        layout.setSpacing(6)

        # AOI mode selector
        mode_row = QHBoxLayout()
        mode_lbl = QLabel("AOI mode:")
        mode_lbl.setStyleSheet(f"color: {_C_MUTED}; font-size: 11px;")
        mode_row.addWidget(mode_lbl)

        self._aoi_mode = QComboBox()
        self._aoi_mode.addItems(["Box AOI", "Polygon AOI"])
        self._aoi_mode.setCurrentIndex(0)
        self._aoi_mode.setFixedWidth(106)
        mode_row.addWidget(self._aoi_mode)
        mode_row.addStretch()
        layout.addLayout(mode_row)

        # Leg spacing
        spacing_row = QHBoxLayout()
        spacing_lbl = QLabel("Leg spacing:")
        spacing_lbl.setStyleSheet(f"color: {_C_MUTED}; font-size: 11px;")
        spacing_row.addWidget(spacing_lbl)

        self._spacing_spin = QSpinBox()
        self._spacing_spin.setRange(5, 500)
        self._spacing_spin.setValue(20)
        self._spacing_spin.setSuffix(" m")
        self._spacing_spin.setFixedWidth(72)
        self._spacing_spin.valueChanged.connect(self._on_spacing_changed)
        spacing_row.addWidget(self._spacing_spin)
        spacing_row.addStretch()
        layout.addLayout(spacing_row)

        # Action buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)

        draw_btn = QPushButton("Draw Area")
        draw_btn.setFixedWidth(90)
        draw_btn.clicked.connect(self._on_draw)
        btn_row.addWidget(draw_btn)

        self._upload_btn = QPushButton("Upload")
        self._upload_btn.setFixedWidth(68)
        self._upload_btn.setEnabled(False)
        self._upload_btn.setStyleSheet("""
            QPushButton:enabled {
                background-color: #1f6feb;
                color: #ffffff;
                border: 1px solid #388bfd;
            }
            QPushButton:enabled:hover {
                background-color: #388bfd;
                border-color: #58a6ff;
            }
            QPushButton:disabled {
                background-color: #161b22;
                color: #484f58;
                border: 1px solid #21262d;
            }
        """)
        self._upload_btn.clicked.connect(self._on_upload)
        btn_row.addWidget(self._upload_btn)

        self._clear_btn = QPushButton("Clear")
        self._clear_btn.setFixedWidth(56)
        self._clear_btn.clicked.connect(self._on_clear)
        btn_row.addWidget(self._clear_btn)

        btn_row.addStretch()
        layout.addLayout(btn_row)

        # Planning status label
        self._plan_status = QLabel(
            "Draw a search area on the map."
        )
        self._plan_status.setStyleSheet(f"color: {_C_MUTED}; font-size: 10px;")
        self._plan_status.setWordWrap(True)
        layout.addWidget(self._plan_status)

        # Wire upload enablement
        bus.vehicle_connected.connect(self._refresh_upload_btn)
        bus.vehicle_disconnected.connect(self._refresh_upload_btn)

        return box

    # ── report section ────────────────────────────────────────────────────────

    def _build_report_section(self) -> QGroupBox:
        box = QGroupBox("Mission Report")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(8, 10, 8, 8)

        self._report_text = QTextEdit()
        self._report_text.setReadOnly(True)
        self._report_text.setFixedHeight(100)
        self._report_text.setStyleSheet(
            f"background: {_C_BG}; color: {_C_TEXT}; font-size: 11px; border: none;"
        )
        layout.addWidget(self._report_text)

        box.setVisible(False)
        return box

    # ── sim slots ─────────────────────────────────────────────────────────────

    def _on_start_sim(self) -> None:
        self._start_btn.setEnabled(False)
        self._world_combo.setEnabled(False)
        self._start_btn.setText("Starting…")
        self._sim.start(world=self._world_combo.currentText())

    def _on_sim_ready(self) -> None:
        self._start_btn.setText("Running")
        self._world_combo.setEnabled(False)

    # ── map toggle ────────────────────────────────────────────────────────────

    _map_is_dark = True

    def _on_map_toggle(self) -> None:
        self._map.toggle_map_style()
        if self._map_is_dark:
            self._map_toggle_btn.setText("🛰")
            self._map_toggle_btn.setToolTip("Switch to Dark Tactical")
        else:
            self._map_toggle_btn.setText("🌙")
            self._map_toggle_btn.setToolTip("Switch to Satellite")
        self._map_is_dark = not self._map_is_dark

    # ── telemetry / map slots ─────────────────────────────────────────────────

    def _on_telemetry_snapshot(self, data: dict) -> None:
        self._last_telemetry = dict(data)

    def _on_telemetry_map(self, data: dict) -> None:
        lat     = data.get("lat")
        lon     = data.get("lon")
        heading = data.get("heading")
        if isinstance(lat, float) and isinstance(lon, float) and isinstance(heading, float):
            self._last_drone_pos = (lat, lon)
            self._map.update_drone_marker(lat, lon, heading)

    def _on_vehicle_disconnected(self) -> None:
        self._map.hide_drone_marker()

    def _on_waypoints_ready(self, waypoints: list) -> None:
        self._map.set_waypoints(waypoints)

    def _on_waypoint_advanced(self, index: int) -> None:
        self._map.set_active_waypoint(index)
        if index > 0:
            self._map.mark_waypoint_complete(index - 1)

    # ── mission map events ────────────────────────────────────────────────────

    def _on_mission_started(self) -> None:
        self._mission_start_time = time.time()
        self._map.clear_mission_state()
        self._map.set_active_waypoint(0)

    # ── planning slots ────────────────────────────────────────────────────────

    def _on_draw(self) -> None:
        if self._aoi_mode.currentText() == "Box AOI":
            self._map.enable_box_draw_mode()
        else:
            self._map.enable_draw_mode()

    def _on_clear(self) -> None:
        self._polygon = []
        self._offsets = []
        self._map.clear_map()
        self._refresh_upload_btn()
        self._set_plan_status("Draw a search area on the map.")

    def _on_polygon(self, vertices: list) -> None:
        print(f"[plan] _on_polygon called: {len(vertices)} raw vertices", file=sys.stderr, flush=True)
        if len(vertices) < 3:
            print(f"[plan] _on_polygon: rejected (< 3 vertices)", file=sys.stderr, flush=True)
            return
        self._polygon = _convex_hull(vertices)
        print(f"[plan] _on_polygon: hull has {len(self._polygon)} vertices: {self._polygon}", file=sys.stderr, flush=True)
        self._generate_and_display(self._spacing_spin.value())

    def _on_spacing_changed(self, value: int) -> None:
        if self._polygon:
            self._generate_and_display(value)

    def _generate_and_display(self, spacing_m: int) -> None:
        polygon_tuples = [tuple(p) for p in self._polygon]
        print(f"[plan] generate_lawnmower input: {len(polygon_tuples)} verts, spacing={spacing_m}", file=sys.stderr, flush=True)
        self._offsets  = generate_lawnmower(polygon_tuples, float(spacing_m))
        print(f"[plan] generate_lawnmower output: {len(self._offsets)} offsets", file=sys.stderr, flush=True)
        center      = polygon_center(polygon_tuples)
        preview_wps = offsets_to_latlon(center, self._offsets)
        print(f"[plan] preview_wps: {len(preview_wps)}", file=sys.stderr, flush=True)
        self._map.set_polygon(self._polygon)
        self._map.set_waypoints(preview_wps)
        n = len(self._offsets)
        self._set_plan_status(
            f"{n} waypoints  ({spacing_m} m spacing) — "
            "anchored at AOI center."
        )
        print(f"[plan] pre-refresh: offsets={len(self._offsets)} drone={self._connector.drone!r}", file=sys.stderr, flush=True)
        self._refresh_upload_btn()

    def _on_upload(self) -> None:
        if not self._connector.drone or not self._connector.loop:
            self._set_plan_status("No drone connected.", error=True)
            return

        from mission.planning.uploader import upload_geofence, upload_mission

        polygon = [tuple(p) for p in self._polygon]
        offsets = list(self._offsets)
        origin  = polygon_center(polygon)

        async def _upload():
            await upload_geofence(self._connector.drone, polygon)
            return await upload_mission(self._connector.drone, offsets, origin)

        self._upload_btn.setEnabled(False)
        self._set_plan_status("Uploading mission…")
        future = asyncio.run_coroutine_threadsafe(_upload(), self._connector.loop)
        future.add_done_callback(self._on_upload_done)

    def _on_upload_done(self, future) -> None:
        try:
            waypoints = future.result()
            bus.mission_waypoints_ready.emit(waypoints)
            self._upload_result.emit(
                True, f"Uploaded {len(waypoints)} waypoints + geofence."
            )
        except Exception as e:
            self._upload_result.emit(False, f"Upload failed: {e}")

    @Slot(bool, str)
    def _on_upload_result(self, success: bool, msg: str) -> None:
        print(f"[upload] _on_upload_result: success={success} msg={msg!r}", file=sys.stderr, flush=True)
        self._set_plan_status(msg, error=not success)
        if success:
            bus.mission_uploaded.emit()
        else:
            self._refresh_upload_btn()

    def _refresh_upload_btn(self) -> None:
        has_offsets = bool(self._offsets)
        has_drone   = self._connector.drone is not None
        enabled     = has_offsets and has_drone
        print(f"[upload] _refresh_upload_btn: offsets={has_offsets} drone={has_drone} -> enabled={enabled}", file=sys.stderr, flush=True)
        self._upload_btn.setEnabled(enabled)
        print(f"[upload] _refresh_upload_btn: btn.isEnabled()={self._upload_btn.isEnabled()}", file=sys.stderr, flush=True)

    def _set_plan_status(self, msg: str, error: bool = False) -> None:
        color = _C_DANGER if error else _C_MUTED
        self._plan_status.setStyleSheet(f"color: {color}; font-size: 10px;")
        self._plan_status.setText(msg)

    # ── AI assist ─────────────────────────────────────────────────────────────

    def _refresh_ai_panel(self) -> None:
        available = bool(self._ai_service and self._ai_service.available)
        self._ai_input.setVisible(available)
        self._ai_btn.setVisible(available)
        if not available:
            self._ai_status.setText("AI unavailable — draw mission manually")
        else:
            self._ai_status.setText("")

    def _on_generate_mission(self) -> None:
        if not self._ai_service or not self._ai_service.available:
            self._refresh_ai_panel()
            return

        desc = self._ai_input.text().strip()
        if not desc:
            self._ai_status.setText("Enter a mission description first.")
            return

        if not self._connector.loop:
            self._ai_status.setText("No event loop — connect a drone first.")
            return

        self._ai_btn.setEnabled(False)
        self._ai_status.setText("Thinking…")

        future = asyncio.run_coroutine_threadsafe(
            self._ai_service.assistant.assist_mission(desc, self._last_drone_pos),
            self._connector.loop,
        )
        future.add_done_callback(self._on_ai_future_done)

    def _on_ai_future_done(self, future) -> None:
        try:
            result = future.result()
        except Exception as e:
            result = str(e)
        self._ai_result.emit(result)

    @Slot(object)
    def _on_ai_result(self, result) -> None:
        self._ai_btn.setEnabled(True)

        if isinstance(result, str):
            self._ai_status.setText(f"Error: {result}")
            return

        try:
            polygon   = result["polygon"]
            spacing_m = float(result["leg_spacing_m"])
        except (KeyError, TypeError, ValueError) as e:
            self._ai_status.setText(f"Invalid AI response: {e}")
            return

        self._polygon = polygon
        self._spacing_spin.setValue(int(max(5, min(500, spacing_m))))
        self._map.set_polygon(polygon)
        self._generate_and_display(self._spacing_spin.value())
        self._ai_status.setText(
            f"Generated ({len(polygon)}-vertex polygon, {spacing_m:.0f} m legs)."
        )

    # ── mission report ────────────────────────────────────────────────────────

    def _on_mission_completed(self) -> None:
        if not self._ai_service or not self._ai_service.assistant:
            return

        self._map.clear_mission_state()

        telemetry = dict(self._last_telemetry)
        if self._mission_start_time is not None:
            telemetry["mission_duration_s"] = round(
                time.time() - self._mission_start_time, 1
            )
            self._mission_start_time = None

        self._report_text.setPlainText("Generating mission report…")
        self._report_group.setVisible(True)

        future = asyncio.run_coroutine_threadsafe(
            self._ai_service.assistant.generate_mission_report(telemetry),
            self._connector.loop,
        )
        future.add_done_callback(self._on_report_future_done)

    def _on_report_future_done(self, future) -> None:
        try:
            report = future.result()
        except Exception as e:
            report = f"Report generation failed: {e}"
        self._report_ready.emit(report)

    def _on_report_ready(self, report: str) -> None:
        self._report_text.setPlainText(report)
        self._report_group.setVisible(True)
        self._save_report(report)

    def _save_report(self, report: str) -> None:
        import datetime
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        try:
            _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
            (_REPORTS_DIR / f"mission_report_{ts}.txt").write_text(report, encoding="utf-8")
        except OSError:
            pass
