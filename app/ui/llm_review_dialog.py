"""
Pre-mission LLM review dialog.

Modal dialog that displays a MissionRiskReview produced by LLMReviewService.
The operator must explicitly acknowledge the review before the mission can be
uploaded. If the review is blocked (risk_level == BLOCKED or blocking_items is
non-empty), the acknowledge button is disabled and the operator must cancel.

Structure mirrors PreflightDialog: scrollable section list, summary group,
Cancel / Confirm buttons.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.models.domain import MissionRiskReview, RiskLevel


# ── Risk-level palette ────────────────────────────────────────────────────────

_RISK_STYLE = {
    RiskLevel.LOW:     ("LOW",     "color: #4caf50; background: #1b3a1f;"),
    RiskLevel.MEDIUM:  ("MEDIUM",  "color: #ffc107; background: #3a2d00;"),
    RiskLevel.HIGH:    ("HIGH",    "color: #ff9800; background: #3a1e00;"),
    RiskLevel.BLOCKED: ("BLOCKED", "color: #f44336; background: #3a1010;"),
}

_CONFIRM_ENABLED_STYLE = (
    "background: #2e7d32; color: #fff; font-weight: bold;"
    "border-radius: 4px; padding: 4px 12px;"
)
_CONFIRM_DISABLED_STYLE = (
    "background: #444; color: #777; border-radius: 4px; padding: 4px 12px;"
)


class LLMReviewDialog(QDialog):
    """Modal pre-mission LLM review.

    Parameters
    ----------
    review   The MissionRiskReview to present. The dialog never mutates it.
    """

    def __init__(self, review: MissionRiskReview, parent=None) -> None:
        super().__init__(parent)
        self._review = review

        self.setWindowTitle("Pre-Mission LLM Review")
        self.setMinimumWidth(620)
        self.setMinimumHeight(480)
        self.setModal(True)

        self._build_ui()
        self._populate()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(10)
        root.setContentsMargins(16, 16, 16, 16)

        # Title row: heading + risk badge + model
        title_row = QHBoxLayout()
        title = QLabel("Pre-Mission LLM Review")
        title.setStyleSheet(
            "font-size: 15px; font-weight: bold; letter-spacing: 1px;"
        )
        title_row.addWidget(title)
        title_row.addSpacing(12)

        self._risk_badge = QLabel("---")
        self._risk_badge.setFixedWidth(86)
        self._risk_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_row.addWidget(self._risk_badge)

        title_row.addStretch()

        self._model_lbl = QLabel("")
        self._model_lbl.setStyleSheet("color: #888; font-size: 11px;")
        title_row.addWidget(self._model_lbl)

        root.addLayout(title_row)

        # Scrollable section content
        self._sections_widget = QWidget()
        self._sections_layout = QVBoxLayout(self._sections_widget)
        self._sections_layout.setSpacing(10)
        self._sections_layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(self._sections_widget)
        root.addWidget(scroll, stretch=1)

        # Acknowledgment note + buttons
        note = QLabel(
            "This is an advisory review. The remote pilot in command "
            "is responsible for the safety of the flight."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #888; font-size: 11px;")
        root.addWidget(note)

        btn_row = QHBoxLayout()
        btn_row.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setFixedWidth(90)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        self._confirm_btn = QPushButton("Acknowledge && Upload")
        self._confirm_btn.setFixedWidth(190)
        self._confirm_btn.setDefault(True)
        self._confirm_btn.clicked.connect(self._on_confirm)
        btn_row.addWidget(self._confirm_btn)

        root.addLayout(btn_row)

    # ── populate ──────────────────────────────────────────────────────────────

    def _populate(self) -> None:
        review = self._review

        # Risk badge
        text, style = _RISK_STYLE.get(review.risk_level, _RISK_STYLE[RiskLevel.MEDIUM])
        self._risk_badge.setText(text)
        self._risk_badge.setStyleSheet(
            style + "font-weight: bold; font-size: 11px;"
            " border-radius: 4px; padding: 3px 6px; letter-spacing: 1px;"
        )

        # Model
        self._model_lbl.setText(f"via {review.llm_model}" if review.llm_model else "")

        # Sections
        if review.plain_english_summary:
            self._sections_layout.addWidget(
                _summary_section("Summary", review.plain_english_summary)
            )

        if review.blocking_items:
            self._sections_layout.addWidget(
                _list_section("Blocking Items", review.blocking_items, danger=True)
            )

        if review.warnings:
            findings = [(f.category, f.message) for f in review.warnings]
            self._sections_layout.addWidget(
                _findings_section("Warnings", findings)
            )

        if review.regulatory_flags:
            self._sections_layout.addWidget(
                _list_section("Regulatory Flags", review.regulatory_flags)
            )

        if review.vehicle_readiness_flags:
            self._sections_layout.addWidget(
                _list_section("Vehicle Readiness", review.vehicle_readiness_flags)
            )

        if review.airspace_weather_flags:
            self._sections_layout.addWidget(
                _list_section("Airspace / Weather", review.airspace_weather_flags)
            )

        if review.missing_information:
            self._sections_layout.addWidget(
                _list_section("Missing Information", review.missing_information)
            )

        if review.suggested_operator_questions:
            self._sections_layout.addWidget(
                _list_section(
                    "Suggested Operator Questions",
                    review.suggested_operator_questions,
                )
            )

        # If no findings at all, show an explicit "nothing flagged" note.
        if self._sections_layout.count() == 0:
            note = QLabel("No findings reported by the review.")
            note.setStyleSheet("color: #888; font-size: 12px;")
            self._sections_layout.addWidget(note)

        self._sections_layout.addStretch()

        # Confirm button state
        blocked = review.is_blocked
        self._confirm_btn.setEnabled(not blocked)
        self._confirm_btn.setStyleSheet(
            _CONFIRM_DISABLED_STYLE if blocked else _CONFIRM_ENABLED_STYLE
        )
        self._confirm_btn.setToolTip(
            "Review reports blocking items — cancel and revise the mission"
            if blocked else ""
        )

    # ── confirm handler ───────────────────────────────────────────────────────

    def _on_confirm(self) -> None:
        # Stamp the acknowledgment onto the review so downstream consumers
        # (logging, post-flight storage) can record that the operator accepted it.
        self._review.operator_acknowledged = True
        self.accept()


# ── section builders ─────────────────────────────────────────────────────────


def _section_header(title: str) -> QLabel:
    lbl = QLabel(title.upper())
    lbl.setStyleSheet(
        "color: #8b949e; font-size: 10px; letter-spacing: 1px;"
        " font-weight: bold; margin-bottom: 2px;"
    )
    return lbl


def _summary_section(title: str, text: str) -> QWidget:
    wrap = QWidget()
    layout = QVBoxLayout(wrap)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(4)

    layout.addWidget(_section_header(title))

    body = QLabel(text)
    body.setWordWrap(True)
    body.setStyleSheet("color: #e6edf3; font-size: 12px;")
    layout.addWidget(body)
    return wrap


def _list_section(title: str, items: list[str], danger: bool = False) -> QWidget:
    wrap = QWidget()
    layout = QVBoxLayout(wrap)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(2)

    layout.addWidget(_section_header(title))

    color = "#f44336" if danger else "#e6edf3"
    for item in items:
        row = QLabel(f"• {item}")
        row.setWordWrap(True)
        row.setStyleSheet(f"color: {color}; font-size: 12px;")
        layout.addWidget(row)
    return wrap


def _findings_section(title: str, findings: list[tuple[str, str]]) -> QWidget:
    wrap = QWidget()
    layout = QVBoxLayout(wrap)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(3)

    layout.addWidget(_section_header(title))

    for category, message in findings:
        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(8)

        cat_lbl = QLabel(category)
        cat_lbl.setFixedWidth(110)
        cat_lbl.setStyleSheet(
            "color: #d29922; font-size: 11px; font-weight: bold;"
        )
        rl.addWidget(cat_lbl)

        msg_lbl = QLabel(message)
        msg_lbl.setWordWrap(True)
        msg_lbl.setStyleSheet("color: #e6edf3; font-size: 12px;")
        rl.addWidget(msg_lbl, stretch=1)

        layout.addWidget(row)
    return wrap
