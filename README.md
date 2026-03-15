# Drone Command Center

Desktop-based autonomous drone command center for simulation and real-world operations. Built with Python and PySide6, integrating PX4 SITL, Gazebo, and MAVSDK for a full ground station and mission-control platform.

![Mode Selection](https://github.com/Colbymitchell1/drone-command-center/raw/master/docs/mode_select.png)

## Overview

This is a serious portfolio project designed to grow into a multi-drone swarm coordination platform with AI-assisted mission logic for search-and-rescue, wildfire response, and field operations use cases.

## Features

- **SIM / REAL mode selection** — shared architecture across both modes
- **Live telemetry dashboard** — lat, lon, alt, speed, heading, battery updated at 4 Hz via MAVSDK
- **System health panel** — real-time PX4, Gazebo, and UDP port status
- **Sim stack launcher** — start/stop PX4 SITL + Gazebo from the UI
- **Event-driven architecture** — Qt signal-based EventBus decouples all subsystems
- **Modular structure** — designed for incremental expansion toward V2/V3

## Tech Stack

| Layer | Technology |
|---|---|
| UI | Python 3.12, PySide6 (Qt6) |
| Vehicle comms | MAVSDK, MAVLink |
| Simulation | PX4 SITL, Gazebo |
| Robotics | ROS2 (planned) |
| Config / persistence | JSON, YAML, SQLite (planned) |

## Project Structure

```
command_center/
├── main.py                        # Entry point
├── app/
│   ├── ui/                        # MainWindow, DashboardView, panels
│   ├── events/event_bus.py        # Central Qt signal bus
│   ├── state/state_store.py       # Single source of truth
│   └── services/sim_controller.py # PX4+Gazebo subprocess management
├── integrations/
│   └── mavsdk/                    # DroneConnector, TelemetryManager
├── mission/                       # Planning, execution, validation (V1)
├── perception/                    # AI/CV pipeline (V2)
└── system/                        # Process manager, health checks
```

## Getting Started

### Requirements

- Python 3.12+
- PySide6
- MAVSDK
- PX4 SITL + Gazebo (for SIM mode)

### Install dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install PySide6 mavsdk
```

### Run

```bash
cd command_center
python main.py
```

On Wayland:
```bash
QT_QPA_PLATFORM=wayland python main.py
```

### SIM mode workflow

1. Launch the app and select **SIM**
2. Click **Start Sim Stack** — starts PX4 SITL + Gazebo (~12s boot)
3. Click **Connect** on port `14540` — live telemetry begins
4. System Health panel turns green as services come online

## Architecture

Communication is fully event-driven. The UI never calls services directly — it emits events, services respond and publish results back through the bus.

```
UI action → EventBus signal → Service/Controller → EventBus signal → UI update
```

Key events: `mode_changed`, `sim_started`, `vehicle_connected`, `telemetry_updated`, `mission_started`, `return_to_home_triggered`

### SIM vs REAL

Both modes share the same interfaces. `DroneConnector` and `TelemetryManager` work identically whether connected to a simulator or a physical vehicle — the rest of the app never needs to know which.

## Roadmap

**V1 (current)** — Desktop shell, mode selection, SIM launcher, live telemetry, system health, modular scaffold

**V2** — Mission planning and execution, map overlays, real hardware support, video ingest, AI perception pipeline

**V3** — Multi-drone coordination, swarm deconfliction, central supervisory control

## License

MIT
