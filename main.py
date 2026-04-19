import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from app.state.state_store import StateStore
from app.ui.main_window import MainWindow
from app.ui.theme import DARK_STYLESHEET


def _apply_dark_palette(app: QApplication) -> None:
    """
    Set a dark QPalette so Qt-native decorations (title bar on some platforms)
    pick up dark colours.  On X11/Wayland this also sets the _GTK_THEME_VARIANT
    and color-scheme hints that GTK-based window managers read.
    """
    p = QPalette()
    # Window chrome
    p.setColor(QPalette.ColorRole.Window,          QColor("#0d1117"))
    p.setColor(QPalette.ColorRole.WindowText,       QColor("#e6edf3"))
    # Widget backgrounds
    p.setColor(QPalette.ColorRole.Base,             QColor("#0d1117"))
    p.setColor(QPalette.ColorRole.AlternateBase,    QColor("#161b22"))
    p.setColor(QPalette.ColorRole.ToolTipBase,      QColor("#161b22"))
    p.setColor(QPalette.ColorRole.ToolTipText,      QColor("#e6edf3"))
    # Text
    p.setColor(QPalette.ColorRole.Text,             QColor("#e6edf3"))
    p.setColor(QPalette.ColorRole.PlaceholderText,  QColor("#484f58"))
    # Buttons
    p.setColor(QPalette.ColorRole.Button,           QColor("#21262d"))
    p.setColor(QPalette.ColorRole.ButtonText,       QColor("#e6edf3"))
    # Selection / highlight
    p.setColor(QPalette.ColorRole.Highlight,        QColor("#1f6feb"))
    p.setColor(QPalette.ColorRole.HighlightedText,  QColor("#ffffff"))
    # Borders / shadows
    p.setColor(QPalette.ColorRole.Mid,              QColor("#30363d"))
    p.setColor(QPalette.ColorRole.Dark,             QColor("#161b22"))
    p.setColor(QPalette.ColorRole.Shadow,           QColor("#010409"))
    # Links
    p.setColor(QPalette.ColorRole.Link,             QColor("#1f6feb"))
    p.setColor(QPalette.ColorRole.LinkVisited,      QColor("#8b949e"))
    app.setPalette(p)

    # Qt 6.5+ color-scheme preference — signals dark mode to the OS/WM.
    # On KDE/GNOME this causes the window manager to render a dark title bar.
    try:
        app.styleHints().setColorScheme(Qt.ColorScheme.Dark)  # type: ignore[attr-defined]
    except AttributeError:
        pass  # Qt < 6.5 — palette above is the best we can do


def main() -> None:
    # Must be set before QApplication is constructed.
    # AA_DontCreateNativeWidgetSiblings: prevents Qt from promoting embedded
    # widgets (including the WebEngine viewport) to top-level native windows.
    # AA_ShareOpenGLContexts: required by QtWebEngine's in-process renderer.
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_DontCreateNativeWidgetSiblings)
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)

    app = QApplication(sys.argv)
    app.setApplicationName("Drone Command Center")
    app.setOrganizationName("Autonomy")
    _apply_dark_palette(app)
    app.setStyleSheet(DARK_STYLESHEET)

    state = StateStore()
    window = MainWindow(state)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
