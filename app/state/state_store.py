from enum import Enum

from PySide6.QtCore import QObject, Signal


class DroneMode(str, Enum):
    SIM = "SIM"
    REAL = "REAL"


class StateStore(QObject):
    """
    Single source of truth for application state.
    Mutate via setters; subscribers react to signals.
    """

    mode_changed = Signal(str)
    connected_changed = Signal(bool)
    armed_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._mode: DroneMode = DroneMode.SIM
        self._connected: bool = False
        self._armed: bool = False

    # --- mode ---

    @property
    def mode(self) -> DroneMode:
        return self._mode

    @mode.setter
    def mode(self, value: DroneMode) -> None:
        if value != self._mode:
            self._mode = value
            self.mode_changed.emit(value.value)

    # --- connection ---

    @property
    def connected(self) -> bool:
        return self._connected

    @connected.setter
    def connected(self, value: bool) -> None:
        if value != self._connected:
            self._connected = value
            self.connected_changed.emit(value)

    # --- armed ---

    @property
    def armed(self) -> bool:
        return self._armed

    @armed.setter
    def armed(self, value: bool) -> None:
        if value != self._armed:
            self._armed = value
            self.armed_changed.emit(value)

    def reset(self) -> None:
        self.connected = False
        self.armed = False
