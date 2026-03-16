import os
import subprocess
from typing import Optional

from PySide6.QtCore import QObject, QTimer, Signal

from app.events.event_bus import bus

STACK_SCRIPT = "/home/colby/start_px4_stack.sh"

# Home position used by both PX4 SITL and the mission planner map center.
# Change here to relocate the simulation world — the map will follow automatically.
SIM_HOME_LAT = 32.9230
SIM_HOME_LON = -117.2590
SIM_HOME_ALT = 0.0


def _udp_port_in_use(port: int) -> bool:
    """True if any process holds a UDP listen socket on this port.

    Uses `ss` to read the kernel socket table directly.  The old socket-bind
    probe was unreliable because PX4 sets SO_REUSEADDR, which lets a second
    socket bind the same port on a specific address even while PX4 already
    holds it on 0.0.0.0 — so the bind would succeed (= "free") when PX4
    was actually up.
    """
    result = subprocess.run(
        ["ss", "-ulnH", f"sport = :{port}"],
        capture_output=True,
        text=True,
    )
    return bool(result.stdout.strip())


def _process_running(pattern: str) -> bool:
    return (
        subprocess.call(
            ["pgrep", "-f", pattern],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        == 0
    )


class SimController(QObject):
    """
    Manages the PX4 SITL + Gazebo subprocess stack.

    Emits health_changed every poll cycle so the UI can stay current
    without polling itself. Also emits bus.sim_started / bus.sim_stopped.
    """

    health_changed = Signal(dict)  # {"px4": bool, "gazebo": bool, "udp_14540": bool, "udp_14550": bool}

    _PX4_PATTERN = "px4_sitl_default/bin/px4"
    _GZ_PATTERN = "gz sim"
    _BOOT_DELAY_MS = 12_000
    _POLL_INTERVAL_MS = 3_000

    def __init__(self, parent=None):
        super().__init__(parent)
        self._process: Optional[subprocess.Popen] = None

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(self._POLL_INTERVAL_MS)
        self._poll_timer.timeout.connect(self._poll)

    # ── public API ────────────────────────────────────────────────────────────

    def start(self, world: str = "baylands") -> None:
        if self._process and self._process.poll() is None:
            return  # already running
        env = os.environ.copy()
        env["WEBKIT_DISABLE_COMPOSITING_MODE"] = "1"
        env.setdefault("QT_QPA_PLATFORM", "xcb")
        # Spawn the sim drone at the same coordinates shown on the mission planner map
        # so that uploaded absolute waypoints match the drone's actual GPS position.
        env["PX4_HOME_LAT"] = str(SIM_HOME_LAT)
        env["PX4_HOME_LON"] = str(SIM_HOME_LON)
        env["PX4_HOME_ALT"] = str(SIM_HOME_ALT)
        self._process = subprocess.Popen(
            ["bash", STACK_SCRIPT, world],
            env=env,
            start_new_session=True,  # os.setsid() — own process group, detached from our app
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._poll_timer.start()
        # PX4 + Gazebo take ~12 s to boot; defer the event until then
        QTimer.singleShot(self._BOOT_DELAY_MS, bus.sim_started.emit)

    def stop(self) -> None:
        self._poll_timer.stop()
        if self._process and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
        self._process = None
        bus.sim_stopped.emit()

    def get_health(self) -> dict:
        return {
            "px4":       _process_running(self._PX4_PATTERN),
            "gazebo":    _process_running(self._GZ_PATTERN),
            "udp_14540": _udp_port_in_use(14540),
            "udp_14550": _udp_port_in_use(14550),
        }

    # ── internals ─────────────────────────────────────────────────────────────

    def _poll(self) -> None:
        self.health_changed.emit(self.get_health())
