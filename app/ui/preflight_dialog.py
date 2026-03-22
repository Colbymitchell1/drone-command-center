"""
Pre-flight validation dialog.

Shows each check as a coloured row (PASS/WARN/FAIL), a flight-estimate
summary, and lets the operator confirm launch or cancel.

'Confirm and Launch' is only enabled when no FAIL checks exist.
'Re-run Checks' re-executes all checks against the live drone without
closing the dialog, so the operator can fix an issue and verify on the spot.
"""

import asyncio

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)
from mavsdk import System

from mission.validation.preflight import (
    CheckStatus,
    PreflightChecker,
    PreflightResult,
    RTL_RESERVE_PCT,
    SAFETY_MARGIN_PCT,
)

# ── Status colour palette ─────────────────────────────────────────────────────

_BADGE_STYLE = {
    CheckStatus.PASS: ("color: #4caf50; background: #1b3a1f;"),
    CheckStatus.WARN: ("color: #ffc107; background: #3a2d00;"),
    CheckStatus.FAIL: ("color: #f44336; background: #3a1010;"),
}

_CONFIRM_ENABLED_STYLE = (
    "background: #2e7d32; color: #fff; font-weight: bold;"
    "border-radius: 4px; padding: 4px 12px;"
)
_CONFIRM_DISABLED_STYLE = (
    "background: #444; color: #777; border-radius: 4px; padding: 4px 12px;"
)


# ── PreflightDialog ───────────────────────────────────────────────────────────

class PreflightDialog(QDialog):
    """
    Modal dialog that presents pre-flight check results.

    Parameters
    ----------
    result          Initial PreflightResult to display.
    drone           Live MAVSDK System (needed to re-run checks).
    mission_offsets (north_m, east_m) offset list for the upcoming mission.
    leg_spacing_m   Leg spacing used for this mission (informational).
    checker         PreflightChecker instance that owns session state.
    loop            asyncio event loop the MAVSDK drone runs on.
    """

    # Carries PreflightResult | None from asyncio thread back to Qt thread
    _rerun_done: Signal = Signal(object)

    def __init__(
        self,
        result: PreflightResult,
        drone: System,
        mission_offsets: list,
        leg_spacing_m: float,
        checker: PreflightChecker,
        loop: asyncio.AbstractEventLoop,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._drone   = drone
        self._offsets = mission_offsets
        self._spacing = leg_spacing_m
        self._checker = checker
        self._loop    = loop

        self.setWindowTitle("Pre-flight Checks")
        self.setMinimumWidth(560)
        self.setModal(True)

        self._build_ui()
        self._rerun_done.connect(self._on_rerun_done)
        self._populate(result)

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(12)
        root.setContentsMargins(16, 16, 16, 16)

        # Title
        title = QLabel("Pre-flight Validation")
        title.setStyleSheet(
            "font-size: 15px; font-weight: bold; letter-spacing: 1px;"
        )
        root.addWidget(title)

        # Scrollable check rows
        self._checks_widget = QWidget()
        self._checks_layout = QVBoxLayout(self._checks_widget)
        self._checks_layout.setSpacing(4)
        self._checks_layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(self._checks_widget)
        scroll.setMinimumHeight(170)
        root.addWidget(scroll)

        # Flight-estimate summary
        summary = QGroupBox("Flight Estimate")
        sl = QVBoxLayout(summary)
        sl.setSpacing(3)

        self._lbl_time      = QLabel()
        self._lbl_drain     = QLabel()
        self._lbl_required  = QLabel()
        self._lbl_available = QLabel()

        for lbl in (
            self._lbl_time,
            self._lbl_drain,
            self._lbl_required,
            self._lbl_available,
        ):
            lbl.setStyleSheet("font-size: 12px;")
            sl.addWidget(lbl)

        root.addWidget(summary)

        # Button row
        btn_row = QHBoxLayout()

        self._rerun_btn = QPushButton("Re-run Checks")
        self._rerun_btn.setFixedWidth(130)
        self._rerun_btn.clicked.connect(self._on_rerun)
        btn_row.addWidget(self._rerun_btn)
        btn_row.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setFixedWidth(90)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        self._confirm_btn = QPushButton("Confirm and Launch")
        self._confirm_btn.setFixedWidth(160)
        self._confirm_btn.setDefault(True)
        self._confirm_btn.clicked.connect(self.accept)
        btn_row.addWidget(self._confirm_btn)

        root.addLayout(btn_row)

    # ── populate ──────────────────────────────────────────────────────────────

    def _populate(self, result: PreflightResult) -> None:
        # Remove old check rows
        while self._checks_layout.count():
            item = self._checks_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for check in result.checks:
            self._checks_layout.addWidget(_make_check_row(check))
        self._checks_layout.addStretch()

        # Summary labels
        if result.is_sim_battery:
            avail_text = "SIM (not measured)"
        elif result.available_battery_pct < 0:
            avail_text = "unavailable"
        else:
            avail_text = f"{result.available_battery_pct:.0f}%"

        self._lbl_time.setText(
            f"Estimated flight time:         {result.estimated_flight_time_min:.1f} min"
        )
        self._lbl_drain.setText(
            f"Estimated battery drain:      ~{result.estimated_battery_pct:.0f}%"
        )
        self._lbl_required.setText(
            f"Required battery:               {result.required_battery_pct:.0f}%"
            f"  (mission + {RTL_RESERVE_PCT:.0f}% RTL + {SAFETY_MARGIN_PCT:.0f}% margin)"
        )
        self._lbl_available.setText(
            f"Available battery:               {avail_text}"
        )

        # Confirm button state
        can = result.can_launch
        self._confirm_btn.setEnabled(can)
        self._confirm_btn.setStyleSheet(
            _CONFIRM_ENABLED_STYLE if can else _CONFIRM_DISABLED_STYLE
        )
        self._confirm_btn.setToolTip(
            "" if can else "Resolve all FAIL checks before launching"
        )

    # ── re-run checks ─────────────────────────────────────────────────────────

    def _on_rerun(self) -> None:
        self._rerun_btn.setEnabled(False)
        self._rerun_btn.setText("Checking…")
        self._confirm_btn.setEnabled(False)
        self._confirm_btn.setStyleSheet(_CONFIRM_DISABLED_STYLE)

        future = asyncio.run_coroutine_threadsafe(
            self._checker.run_checks(self._drone, self._offsets, self._spacing),
            self._loop,
        )
        future.add_done_callback(self._on_rerun_future_done)

    def _on_rerun_future_done(self, future) -> None:
        """Called from the asyncio thread — emit signal to hand off to Qt thread."""
        try:
            result = future.result()
        except Exception:
            result = None
        self._rerun_done.emit(result)

    @Slot(object)
    def _on_rerun_done(self, result) -> None:
        self._rerun_btn.setEnabled(True)
        self._rerun_btn.setText("Re-run Checks")
        if result is not None:
            self._populate(result)


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_check_row(check) -> QWidget:
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(4, 2, 4, 2)
    layout.setSpacing(10)

    # Coloured status badge
    badge = QLabel(check.status.value)
    badge.setFixedWidth(44)
    badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
    badge.setStyleSheet(
        _BADGE_STYLE[check.status]
        + " font-weight: bold; font-size: 10px;"
          " border-radius: 3px; padding: 2px 4px; letter-spacing: 1px;"
    )

    # Check name
    name_lbl = QLabel(check.name)
    name_lbl.setFixedWidth(138)
    name_lbl.setStyleSheet("font-weight: bold; font-size: 12px;")

    # Detail message
    msg_lbl = QLabel(check.message)
    msg_lbl.setStyleSheet("font-size: 12px; color: #ccc;")
    msg_lbl.setWordWrap(True)

    layout.addWidget(badge)
    layout.addWidget(name_lbl)
    layout.addWidget(msg_lbl, stretch=1)
    return row
