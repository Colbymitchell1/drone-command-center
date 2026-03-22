from PySide6.QtCore import QObject, Signal


class EventBus(QObject):
    """Central event bus. Publish by emitting a signal; subscribe with .connect()."""

    # Mode / lifecycle
    mode_changed = Signal(str)          # "SIM" | "REAL"
    sim_started = Signal()
    sim_stopped = Signal()

    # Vehicle
    vehicle_connected = Signal()
    vehicle_disconnected = Signal()
    vehicle_error = Signal(str)

    # Telemetry
    telemetry_updated = Signal(dict)

    # Mission
    mission_uploaded = Signal()
    mission_started = Signal()
    mission_completed = Signal()
    mission_aborted = Signal(str)       # reason string
    waypoint_advanced = Signal(int)     # progress.current — index of next target waypoint

    # Battery
    battery_warning = Signal(float)     # pct — dropped below WARNING_PCT
    battery_critical = Signal(float)    # pct — dropped below CRITICAL_PCT

    # Autonomy / safety
    target_detected = Signal(dict)
    return_to_home_triggered = Signal()


# Module-level singleton — import `bus` wherever you need it
bus = EventBus()
