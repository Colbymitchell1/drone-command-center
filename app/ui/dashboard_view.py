import asyncio

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.events.event_bus import bus
from app.services.sim_controller import SimController
from app.state.state_store import DroneMode, StateStore
from integrations.mavsdk.connector import DroneConnector
from mission.execution.executor import LawnmowerExecutor, MissionStatus
from mission.execution.mission_runner import UploadedMissionRunner
from mission.validation.preflight import PreflightChecker
from app.ui.mission_planner_view import MissionPlannerView


# ── shared helpers ────────────────────────────────────────────────────────────

def _make_status_label() -> QLabel:
    lbl = QLabel("---")
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lbl.setFixedWidth(64)
    _apply_status(lbl, None)
    return lbl


def _apply_status(lbl: QLabel, ok: bool | None) -> None:
    if ok is None:
        color, text = "#888888", "---"
    elif ok:
        color, text = "#4caf50", " OK "
    else:
        color, text = "#f44336", " NO "
    lbl.setText(text)
    lbl.setStyleSheet(
        f"color: {color}; font-weight: bold; font-size: 12px;"
        f"background: #1e1e1e; border-radius: 4px; padding: 2px 6px;"
    )


# ── TelemetryPanel ────────────────────────────────────────────────────────────

class TelemetryPanel(QGroupBox):
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
        layout = QHBoxLayout(self)
        layout.setSpacing(24)

        for key, display_name, unit in self._FIELDS:
            col = QVBoxLayout()
            col.setAlignment(Qt.AlignmentFlag.AlignHCenter)

            name_lbl = QLabel(display_name)
            name_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            name_lbl.setStyleSheet("color: #888; font-size: 10px; letter-spacing: 1px;")

            val_lbl = QLabel("---")
            val_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            val_lbl.setStyleSheet("font-size: 20px; font-weight: bold; min-width: 80px;")
            val_lbl.setProperty("unit", unit)

            self._value_labels[key] = val_lbl
            col.addWidget(name_lbl)
            col.addWidget(val_lbl)
            layout.addLayout(col)

    def _on_telemetry(self, data: dict) -> None:
        for key, lbl in self._value_labels.items():
            if key in data:
                unit = lbl.property("unit") or ""
                raw = data[key]
                # Format floats to a readable precision; skip unit for string sentinels
                if isinstance(raw, float):
                    text = f"{raw:.4f}" if key in ("lat", "lon") else f"{raw:.1f}"
                    lbl.setText(f"{text}{unit}")
                else:
                    lbl.setText(str(raw))


# ── SystemHealthPanel ─────────────────────────────────────────────────────────

class SystemHealthPanel(QGroupBox):
    _ROWS = [
        ("px4",       "PX4 SITL"),
        ("gazebo",    "Gazebo"),
        ("udp_14540", "UDP 14540"),
        ("udp_14550", "UDP 14550"),
    ]

    def __init__(self, sim_controller: SimController, parent=None):
        super().__init__("System Health", parent)
        self._sim = sim_controller
        self._indicators: dict[str, QLabel] = {}
        self._build_ui()

        # Subscribe to SimController's push updates
        self._sim.health_changed.connect(self._apply_health)

        # Also do an immediate poll so the panel isn't blank on first open
        QTimer.singleShot(0, lambda: self._apply_health(self._sim.get_health()))

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        for key, label_text in self._ROWS:
            row = QHBoxLayout()
            name = QLabel(label_text)
            name.setFixedWidth(100)
            status = _make_status_label()
            self._indicators[key] = status
            row.addWidget(name)
            row.addWidget(status)
            row.addStretch()
            layout.addLayout(row)

        layout.addStretch()

    def _apply_health(self, health: dict) -> None:
        for key, lbl in self._indicators.items():
            if key in health:
                _apply_status(lbl, health[key])


# ── ConnectPanel ──────────────────────────────────────────────────────────────

class ConnectPanel(QWidget):
    """UDP port input + Connect/Disconnect button + live connection status."""

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

        layout.addWidget(QLabel("UDP Port:"))

        self._port_input = QLineEdit("14540")
        self._port_input.setFixedWidth(64)
        layout.addWidget(self._port_input)

        self._connect_btn = QPushButton("Connect")
        self._connect_btn.setFixedWidth(110)
        self._connect_btn.clicked.connect(self._on_btn_clicked)
        layout.addWidget(self._connect_btn)

        self._status_lbl = QLabel("Disconnected")
        self._status_lbl.setStyleSheet("color: #888; font-size: 12px;")
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
            self._status_lbl.setStyleSheet("color: #888; font-size: 12px;")
            self._connector.connect(port)

    def _on_connected(self) -> None:
        self._connect_btn.setEnabled(True)
        self._connect_btn.setText("Disconnect")
        self._port_input.setEnabled(False)
        self._status_lbl.setText("Connected")
        self._status_lbl.setStyleSheet("color: #4caf50; font-weight: bold; font-size: 12px;")

    def _on_disconnected(self) -> None:
        self._connect_btn.setEnabled(True)
        self._connect_btn.setText("Connect")
        self._port_input.setEnabled(True)
        self._status_lbl.setText("Disconnected")
        self._status_lbl.setStyleSheet("color: #888; font-size: 12px;")

    def _on_error(self, msg: str) -> None:
        self._connect_btn.setEnabled(True)
        self._connect_btn.setText("Connect")
        self._port_input.setEnabled(True)
        self._status_lbl.setText(f"Error: {msg}")
        self._status_lbl.setStyleSheet("color: #f44336; font-size: 12px;")


# ── MissionPanel ──────────────────────────────────────────────────────────────

_STATUS_STYLES = {
    MissionStatus.IDLE:     ("IDLE",     "#888888"),
    MissionStatus.RUNNING:  ("RUNNING",  "#ff9800"),
    MissionStatus.COMPLETE: ("COMPLETE", "#4caf50"),
    MissionStatus.ABORTED:  ("ABORTED",  "#f44336"),
}


class MissionPanel(QGroupBox):
    # Carries PreflightResult | None from the asyncio thread back to Qt thread
    _preflight_done: Signal = Signal(object)

    def __init__(
        self,
        executor: LawnmowerExecutor,
        runner: UploadedMissionRunner,
        connector: DroneConnector,
        planner: "MissionPlannerView",
        parent=None,
    ):
        super().__init__("Mission", parent)
        self._executor  = executor
        self._runner    = runner
        self._connector = connector
        self._planner   = planner
        self._checker   = PreflightChecker()
        self._active    = None   # whichever executor was started most recently
        self._pending   = None   # "lawnmower" | "mission" — waiting for preflight

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

        # Status row
        status_row = QHBoxLayout()
        label = QLabel("STATUS:")
        label.setStyleSheet("color: #888; font-size: 11px; letter-spacing: 1px;")
        status_row.addWidget(label)

        self._status_lbl = QLabel("IDLE")
        self._status_lbl.setFixedWidth(80)
        self._status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_lbl.setStyleSheet(
            "color: #888888; font-weight: bold; font-size: 13px;"
            "background: #1e1e1e; border-radius: 4px; padding: 3px 8px;"
        )
        status_row.addWidget(self._status_lbl)
        status_row.addStretch()

        self._abort_btn = QPushButton("Abort")
        self._abort_btn.setFixedWidth(80)
        self._abort_btn.setEnabled(False)
        self._abort_btn.setStyleSheet("color: #f44336;")
        self._abort_btn.clicked.connect(self._on_abort)
        status_row.addWidget(self._abort_btn)

        # Action row
        action_row = QHBoxLayout()

        self._lawnmower_btn = QPushButton("Start Lawnmower Search")
        self._lawnmower_btn.setFixedWidth(200)
        self._lawnmower_btn.setEnabled(False)
        self._lawnmower_btn.setToolTip("Offboard velocity lawnmower — no upload required")
        self._lawnmower_btn.clicked.connect(self._on_start_lawnmower)
        action_row.addWidget(self._lawnmower_btn)

        self._mission_btn = QPushButton("Start Uploaded Mission")
        self._mission_btn.setFixedWidth(200)
        self._mission_btn.setEnabled(False)
        self._mission_btn.setToolTip("Execute the mission currently uploaded to the drone")
        self._mission_btn.clicked.connect(self._on_start_mission)
        action_row.addWidget(self._mission_btn)

        action_row.addStretch()

        root.addLayout(status_row)
        root.addLayout(action_row)

    # ── status display ────────────────────────────────────────────────────────

    def _set_status(self, status: MissionStatus) -> None:
        text, color = _STATUS_STYLES[status]
        self._status_lbl.setText(text)
        self._status_lbl.setStyleSheet(
            f"color: {color}; font-weight: bold; font-size: 13px;"
            f"background: #1e1e1e; border-radius: 4px; padding: 3px 8px;"
        )
        running = status == MissionStatus.RUNNING
        self._lawnmower_btn.setEnabled(not running)
        self._mission_btn.setEnabled(not running and self._mission_uploaded)
        self._abort_btn.setEnabled(running)

    # ── vehicle events ────────────────────────────────────────────────────────

    def _on_connected(self) -> None:
        self._lawnmower_btn.setEnabled(True)
        # mission_btn only enabled after a mission has been uploaded this session

    def _on_disconnected(self) -> None:
        self._lawnmower_btn.setEnabled(False)
        self._mission_btn.setEnabled(False)
        self._abort_btn.setEnabled(False)
        self._active  = None
        self._pending = None
        self._set_status(MissionStatus.IDLE)

    def _on_mission_uploaded(self) -> None:
        self._mission_uploaded = True
        if self._lawnmower_btn.isEnabled():   # i.e. connected and not running
            self._mission_btn.setEnabled(True)

    # ── button handlers — trigger preflight before starting ──────────────────

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

    # ── preflight flow ────────────────────────────────────────────────────────

    def _run_preflight(self, offsets: list, spacing_m: float) -> None:
        """Kick off async preflight checks; result delivered via _preflight_done."""
        if not self._connector.drone or not self._connector.loop:
            bus.mission_aborted.emit("No drone connected")
            self._pending = None
            return

        # Disable both start buttons while checks are running
        self._lawnmower_btn.setEnabled(False)
        self._mission_btn.setEnabled(False)

        # Store for use when the dialog opens
        self._pending_offsets  = offsets
        self._pending_spacing  = spacing_m

        future = asyncio.run_coroutine_threadsafe(
            self._checker.run_checks(
                self._connector.drone, offsets, spacing_m
            ),
            self._connector.loop,
        )
        future.add_done_callback(self._on_preflight_future_done)

    def _on_preflight_future_done(self, future) -> None:
        """Called from asyncio thread — hand result off to Qt thread via signal."""
        try:
            result = future.result()
        except Exception:
            result = None
        self._preflight_done.emit(result)

    def _on_preflight_done(self, result) -> None:
        """Runs on Qt main thread — show dialog and act on operator decision."""
        if result is None:
            # Checks raised an unexpected exception; restore buttons and bail
            self._restore_start_buttons()
            self._pending = None
            return

        # Lazy import avoids a circular dependency at module load time
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
        """Re-enable start buttons to the state they should be in at rest."""
        self._lawnmower_btn.setEnabled(True)
        self._mission_btn.setEnabled(self._mission_uploaded)

    # Track whether a mission has been uploaded this session
    _mission_uploaded = False


# ── DashboardView ─────────────────────────────────────────────────────────────

class DashboardView(QWidget):
    def __init__(self, state: StateStore, sim_controller: SimController,
                 connector: DroneConnector, parent=None):
        super().__init__(parent)
        self._state = state
        self._sim = sim_controller
        self._connector = connector
        self._executor = LawnmowerExecutor(self)
        self._runner = UploadedMissionRunner(self)
        self._build_ui()
        self._wire_executors()

    def _wire_executors(self) -> None:
        bus.vehicle_connected.connect(
            lambda: self._executor.bind(self._connector.drone, self._connector.loop)
        )
        bus.vehicle_connected.connect(
            lambda: self._runner.bind(self._connector.drone, self._connector.loop)
        )
        bus.vehicle_disconnected.connect(self._executor.unbind)
        bus.vehicle_disconnected.connect(self._runner.unbind)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(8)

        # ── always-visible header ─────────────────────────────────────────────
        header = QHBoxLayout()
        mode_badge = QLabel(f"MODE: {self._state.mode.value}")
        mode_badge.setStyleSheet(
            "font-weight: bold; font-size: 13px; color: #4caf50; letter-spacing: 1px;"
        )
        self._world_combo = QComboBox()
        self._world_combo.addItems(["baylands", "lawn", "windy", "forest", "default"])
        self._world_combo.setFixedWidth(100)
        self._world_combo.setVisible(self._state.mode == DroneMode.SIM)

        self._start_btn = QPushButton("Start Sim Stack")
        self._start_btn.setFixedWidth(160)
        self._start_btn.clicked.connect(self._on_start_sim)
        self._start_btn.setVisible(self._state.mode == DroneMode.SIM)

        header.addWidget(mode_badge)
        header.addStretch()
        header.addWidget(self._world_combo)
        header.addWidget(self._start_btn)

        connect_row = ConnectPanel(self._connector)

        root.addLayout(header)
        root.addWidget(connect_row)

        # ── tab widget ────────────────────────────────────────────────────────
        tabs = QTabWidget()

        # Create the planner first so MissionPanel can hold a reference to it
        self._planner = MissionPlannerView(self._connector)

        # Tab 0: Overview — telemetry, health, mission execution
        overview = QWidget()
        ov = QVBoxLayout(overview)
        ov.setContentsMargins(0, 8, 0, 0)
        ov.setSpacing(8)
        panels = QHBoxLayout()
        panels.addWidget(TelemetryPanel(), stretch=3)
        panels.addWidget(SystemHealthPanel(self._sim), stretch=1)
        ov.addLayout(panels)
        ov.addWidget(
            MissionPanel(self._executor, self._runner, self._connector, self._planner)
        )
        ov.addStretch()
        tabs.addTab(overview, "Overview")

        # Tab 1: Mission Planner
        tabs.addTab(self._planner, "Mission Planner")

        root.addWidget(tabs, stretch=1)

        # Wire bus events
        bus.sim_started.connect(self._on_sim_ready)

    def _on_start_sim(self) -> None:
        self._start_btn.setEnabled(False)
        self._world_combo.setEnabled(False)
        self._start_btn.setText("Starting…")
        self._sim.start(world=self._world_combo.currentText())

    def _on_sim_ready(self) -> None:
        self._start_btn.setText("Sim Running")
        self._world_combo.setEnabled(False)
