import sys

from PySide6.QtWidgets import QApplication

from app.state.state_store import StateStore
from app.ui.main_window import MainWindow


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("Drone Command Center")
    app.setOrganizationName("Autonomy")

    state = StateStore()
    window = MainWindow(state)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
