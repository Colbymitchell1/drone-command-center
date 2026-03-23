import asyncio

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QRadioButton,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from app.events.event_bus import bus
from app.services.ai_service import AIService
from app.services.sim_controller import SimController
from app.state.state_store import DroneMode, StateStore
from integrations.mavsdk.connector import DroneConnector


class ModeSelectionView(QWidget):
    """Initial screen — operator picks SIM or REAL before entering the dashboard."""

    def __init__(self, state: StateStore, parent=None):
        super().__init__(parent)
        self._state = state
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.setSpacing(12)

        title = QLabel("Autonomous Drone Command Center")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 24px; font-weight: bold;")

        subtitle = QLabel("Select operating mode to continue")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setStyleSheet("color: #888; font-size: 13px;")

        # SIM / REAL radio buttons
        self._sim_radio = QRadioButton("SIM  —  PX4 SITL + Gazebo")
        self._real_radio = QRadioButton("REAL  —  Physical Vehicle")
        self._sim_radio.setChecked(True)

        self._btn_group = QButtonGroup(self)
        self._btn_group.addButton(self._sim_radio)
        self._btn_group.addButton(self._real_radio)
        self._btn_group.buttonClicked.connect(self._on_mode_toggled)

        radio_row = QHBoxLayout()
        radio_row.addStretch()
        radio_row.addWidget(self._sim_radio)
        radio_row.addSpacing(48)
        radio_row.addWidget(self._real_radio)
        radio_row.addStretch()

        # Confirm
        self._launch_btn = QPushButton("Launch Command Center")
        self._launch_btn.setFixedWidth(240)
        self._launch_btn.clicked.connect(self._on_launch)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(self._launch_btn)
        btn_row.addStretch()

        root.addStretch()
        root.addWidget(title)
        root.addSpacing(6)
        root.addWidget(subtitle)
        root.addSpacing(32)
        root.addLayout(radio_row)
        root.addSpacing(24)
        root.addLayout(btn_row)
        root.addStretch()

    def _on_mode_toggled(self, btn: QRadioButton) -> None:
        self._state.mode = (
            DroneMode.SIM if btn is self._sim_radio else DroneMode.REAL
        )

    def _on_launch(self) -> None:
        bus.mode_changed.emit(self._state.mode.value)


class MainWindow(QMainWindow):
    def __init__(self, state: StateStore):
        super().__init__()
        self._state = state
        self._sim_controller = SimController(self)
        self._connector = DroneConnector(self)

        # AI service — loop is already running, probe runs in background.
        self._ai_service = AIService()
        asyncio.run_coroutine_threadsafe(
            self._ai_service.initialise(), self._connector.loop
        )

        self.setWindowTitle("Drone Command Center")
        self.setMinimumSize(960, 640)

        self.setStatusBar(QStatusBar(self))
        self.statusBar().showMessage("Select a mode to begin.")

        self._show_mode_selection()
        self._wire_events()

    def _show_mode_selection(self) -> None:
        self.setCentralWidget(ModeSelectionView(self._state, self))

    def _wire_events(self) -> None:
        bus.mode_changed.connect(self._on_mode_confirmed)
        bus.sim_started.connect(
            lambda: self.statusBar().showMessage(
                f"Mode: {self._state.mode.value}  |  Sim stack ready"
            )
        )

    def _on_mode_confirmed(self, mode: str) -> None:
        from app.ui.dashboard_view import DashboardView
        self.statusBar().showMessage(f"Mode: {mode}  |  Dashboard ready")
        self.setCentralWidget(
            DashboardView(
                self._state, self._sim_controller, self._connector,
                self._ai_service, self
            )
        )
        bus.vehicle_connected.connect(
            lambda: self.statusBar().showMessage(f"Mode: {mode}  |  Vehicle connected")
        )
        bus.vehicle_disconnected.connect(
            lambda: self.statusBar().showMessage(f"Mode: {mode}  |  Vehicle disconnected")
        )
