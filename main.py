import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from app.state.state_store import StateStore
from app.ui.main_window import MainWindow


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

    state = StateStore()
    window = MainWindow(state)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
