"""Global dark stylesheet for the Drone Command Center.

Colors:
    background  #0d1117  — app / window background
    panels      #161b22  — group boxes, side panels
    borders     #30363d  — borders, dividers, splitters
    text        #e6edf3  — primary text
    muted       #8b949e  — secondary / label text
    accent      #1f6feb  — primary action blue
    success     #238636  — armed, OK, complete
    danger      #da3633  — error, abort, fail
    warning     #d29922  — battery warn, amber
    cyan        #00d4ff  — tactical highlights
"""

DARK_STYLESHEET = """

/* ── Base ──────────────────────────────────────────────────────────────────── */

QWidget {
    background-color: #0d1117;
    color: #e6edf3;
    font-family: "Segoe UI", "SF Pro Display", system-ui, sans-serif;
    font-size: 13px;
    border: none;
    outline: none;
}

QMainWindow {
    background-color: #0d1117;
}

QDialog {
    background-color: #0d1117;
}

/* ── Group Boxes ───────────────────────────────────────────────────────────── */

QGroupBox {
    background-color: #161b22;
    border: 1px solid #30363d;
    border-radius: 6px;
    margin-top: 10px;
    padding-top: 10px;
    font-size: 10px;
    font-weight: bold;
    color: #8b949e;
    letter-spacing: 1px;
    text-transform: uppercase;
}

QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 10px;
    top: 2px;
    padding: 0 4px;
    background-color: #161b22;
}

/* ── Buttons ───────────────────────────────────────────────────────────────── */

QPushButton {
    background-color: #21262d;
    color: #e6edf3;
    border: 1px solid #30363d;
    border-radius: 6px;
    padding: 5px 14px;
    font-size: 12px;
    font-weight: 500;
    min-height: 22px;
}

QPushButton:hover {
    background-color: #30363d;
    border-color: #8b949e;
}

QPushButton:pressed {
    background-color: #161b22;
    border-color: #1f6feb;
}

QPushButton:disabled {
    background-color: #161b22;
    color: #484f58;
    border-color: #21262d;
}

/* ── Line Edits ────────────────────────────────────────────────────────────── */

QLineEdit {
    background-color: #0d1117;
    color: #e6edf3;
    border: 1px solid #30363d;
    border-radius: 6px;
    padding: 4px 8px;
    font-size: 12px;
    selection-background-color: #1f6feb;
    selection-color: #ffffff;
    min-height: 20px;
}

QLineEdit:focus {
    border-color: #1f6feb;
}

QLineEdit:disabled {
    background-color: #161b22;
    color: #484f58;
    border-color: #21262d;
}

QLineEdit[placeholderText] {
    color: #484f58;
}

/* ── Spin Box ──────────────────────────────────────────────────────────────── */

QSpinBox {
    background-color: #0d1117;
    color: #e6edf3;
    border: 1px solid #30363d;
    border-radius: 6px;
    padding: 4px 8px;
    font-size: 12px;
    min-height: 20px;
}

QSpinBox:focus {
    border-color: #1f6feb;
}

QSpinBox::up-button, QSpinBox::down-button {
    background-color: #21262d;
    border: none;
    width: 18px;
    border-radius: 3px;
}

QSpinBox::up-button:hover, QSpinBox::down-button:hover {
    background-color: #30363d;
}

QSpinBox::up-arrow {
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-bottom: 5px solid #8b949e;
    width: 0; height: 0;
}

QSpinBox::down-arrow {
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid #8b949e;
    width: 0; height: 0;
}

/* ── Combo Box ─────────────────────────────────────────────────────────────── */

QComboBox {
    background-color: #0d1117;
    color: #e6edf3;
    border: 1px solid #30363d;
    border-radius: 6px;
    padding: 4px 8px;
    font-size: 12px;
    min-height: 20px;
}

QComboBox:focus {
    border-color: #1f6feb;
}

QComboBox:disabled {
    background-color: #161b22;
    color: #484f58;
    border-color: #21262d;
}

QComboBox::drop-down {
    border: none;
    width: 22px;
}

QComboBox QAbstractItemView {
    background-color: #161b22;
    color: #e6edf3;
    border: 1px solid #30363d;
    border-radius: 4px;
    selection-background-color: #1f6feb;
    selection-color: #ffffff;
    outline: none;
    padding: 2px;
}

/* ── Text Edit ─────────────────────────────────────────────────────────────── */

QTextEdit {
    background-color: #0d1117;
    color: #e6edf3;
    border: 1px solid #30363d;
    border-radius: 6px;
    padding: 6px;
    font-size: 12px;
    selection-background-color: #1f6feb;
    selection-color: #ffffff;
}

/* ── Tab Widget ────────────────────────────────────────────────────────────── */

QTabWidget::pane {
    background-color: #161b22;
    border: 1px solid #30363d;
    border-radius: 0 6px 6px 6px;
}

QTabBar {
    background-color: transparent;
}

QTabBar::tab {
    background-color: #0d1117;
    color: #8b949e;
    border: 1px solid #30363d;
    border-bottom: none;
    border-radius: 4px 4px 0 0;
    padding: 6px 16px;
    margin-right: 2px;
    font-size: 12px;
    font-weight: 500;
}

QTabBar::tab:selected {
    background-color: #161b22;
    color: #e6edf3;
    border-bottom-color: #161b22;
}

QTabBar::tab:hover:!selected {
    background-color: #21262d;
    color: #c9d1d9;
}

/* ── Scroll Areas ──────────────────────────────────────────────────────────── */

QScrollArea {
    background-color: transparent;
    border: none;
}

QScrollArea > QWidget > QWidget {
    background-color: transparent;
}

/* ── Scroll Bars ───────────────────────────────────────────────────────────── */

QScrollBar:vertical {
    background-color: transparent;
    width: 8px;
    margin: 0;
}

QScrollBar::handle:vertical {
    background-color: #30363d;
    border-radius: 4px;
    min-height: 20px;
}

QScrollBar::handle:vertical:hover {
    background-color: #484f58;
}

QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical {
    height: 0;
}

QScrollBar:horizontal {
    background-color: transparent;
    height: 8px;
    margin: 0;
}

QScrollBar::handle:horizontal {
    background-color: #30363d;
    border-radius: 4px;
    min-width: 20px;
}

QScrollBar::handle:horizontal:hover {
    background-color: #484f58;
}

QScrollBar::add-line:horizontal,
QScrollBar::sub-line:horizontal {
    width: 0;
}

/* ── Status Bar ────────────────────────────────────────────────────────────── */

QStatusBar {
    background-color: #161b22;
    color: #8b949e;
    border-top: 1px solid #30363d;
    font-size: 12px;
    padding: 2px 8px;
}

QStatusBar::item {
    border: none;
}

/* ── Splitter ──────────────────────────────────────────────────────────────── */

QSplitter::handle {
    background-color: #30363d;
}

QSplitter::handle:horizontal {
    width: 1px;
}

QSplitter::handle:vertical {
    height: 1px;
}

/* ── Labels ────────────────────────────────────────────────────────────────── */

QLabel {
    background-color: transparent;
    color: #e6edf3;
}

/* ── Radio Buttons ─────────────────────────────────────────────────────────── */

QRadioButton {
    color: #e6edf3;
    spacing: 8px;
    font-size: 13px;
    background: transparent;
}

QRadioButton::indicator {
    width: 16px;
    height: 16px;
    border: 2px solid #30363d;
    border-radius: 8px;
    background-color: #0d1117;
}

QRadioButton::indicator:checked {
    background-color: #1f6feb;
    border-color: #1f6feb;
}

QRadioButton::indicator:hover {
    border-color: #8b949e;
}

/* ── Check Boxes ───────────────────────────────────────────────────────────── */

QCheckBox {
    color: #e6edf3;
    spacing: 8px;
    background: transparent;
}

QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border: 2px solid #30363d;
    border-radius: 3px;
    background-color: #0d1117;
}

QCheckBox::indicator:checked {
    background-color: #1f6feb;
    border-color: #1f6feb;
}

/* ── Frames ────────────────────────────────────────────────────────────────── */

QFrame[frameShape="4"],
QFrame[frameShape="5"] {
    color: #30363d;
}

/* ── Tool Tips ─────────────────────────────────────────────────────────────── */

QToolTip {
    background-color: #161b22;
    color: #e6edf3;
    border: 1px solid #30363d;
    border-radius: 4px;
    padding: 4px 8px;
    font-size: 12px;
}

/* ── Menus ─────────────────────────────────────────────────────────────────── */

QMenuBar {
    background-color: #161b22;
    color: #e6edf3;
    border-bottom: 1px solid #30363d;
}

QMenuBar::item:selected {
    background-color: #1f6feb;
}

QMenu {
    background-color: #161b22;
    color: #e6edf3;
    border: 1px solid #30363d;
    border-radius: 4px;
    padding: 4px 0;
}

QMenu::item {
    padding: 5px 24px 5px 16px;
}

QMenu::item:selected {
    background-color: #1f6feb;
}

QMenu::separator {
    height: 1px;
    background-color: #30363d;
    margin: 4px 8px;
}

"""
