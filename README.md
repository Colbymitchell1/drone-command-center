# Autonomous Drone Command Center

A desktop ground station for autonomous drone mission planning, telemetry monitoring, and simulation-based workflow testing.

![Mission Planner](docs/mission_planner.png)

---

## Overview

This project is a PySide6 desktop application built to support core drone operations workflows in simulation. It integrates with PX4 SITL, Gazebo, and MAVSDK to provide an operator-facing interface for mission planning, execution, telemetry monitoring, and safety control.

The goal of the project is to build a practical command interface that can launch a simulated environment, connect to a vehicle, display live telemetry, plan missions from a map, and execute autonomous coverage behavior in a controlled workflow.

This is an active portfolio project and is still under development. The current version focuses on proving the architecture, connectivity, and mission workflow foundation rather than presenting a fully finished product.

---

## Demo

### Screen Recording
https://www.linkedin.com/posts/colby-mitchell-804847128_dronetech-autonomy-uav-ugcPost-7441303931038883840-cZa1?utm_source=share&utm_medium=member_desktop&rcm=ACoAAB9y18QBXRWFyq6ecRSKmHlAtFM-7MV-BZw

Suggested demo flow:
- application startup
- simulation stack launch
- UDP vehicle connection
- mission area selection on the map
- mission upload / mission load
- mission start in Gazebo

### Screenshots
<img width="982" height="700" alt="Screenshot from 2026-03-21 17-40-47" src="https://github.com/user-attachments/assets/d520a6cf-953d-444b-864e-7005c99d05b9" />
<img width="982" height="700" alt="Pasted image" src="https://github.com/user-attachments/assets/1405702a-3c0f-4405-9925-7139fc7c7c58" />



Suggested screenshots:
- main telemetry / overview tab
- mission planner tab with selected polygon
- Gazebo simulation view during mission execution

---

## Current Status

The project is functional in simulation and currently demonstrates the core mission loop:

1. Launch command center
2. Start simulation stack
3. Connect to the vehicle over UDP
4. Draw/select a mission area on the map
5. Upload or load the mission
6. Start the autonomous mission from the main interface

The main issue currently being worked is mission/reference-frame alignment. At this stage, the vehicle can launch and begin the mission flow, but mission execution can diverge because the mission map coordinates and the Gazebo home/reference location are not always aligned correctly.

That is a real systems-integration bug, and solving problems like that is part of the point of this project.

---

## Features

### Mission Planning
- Interactive map interface (Leaflet.js / OpenStreetMap) with real-world coordinates
- Polygon-based search area definition where the operator draws the boundary and the system generates the pattern
- Automatic lawnmower coverage pattern generation fitted to the polygon
- Adjustable leg spacing for coverage density control
- Geofence and mission upload to the drone via MAVSDK

### Mission Execution
- Uploaded mission execution via PX4 mission API
- Manual lawnmower search mode with configurable parameters
- Real-time mission status: IDLE / RUNNING / COMPLETE / ABORTED
- Operator abort with immediate RTL and automatic landing behavior
- Post-mission RTL with retry handling
- Safety-oriented mission stop workflow

### Telemetry & System Health
- Live telemetry display: latitude, longitude, altitude, speed, heading, battery
- System health visibility for PX4 SITL status, Gazebo status, and UDP connectivity
- Vehicle connection status with connect/disconnect control

### Simulation Management
- One-click sim stack launch (PX4 SITL + Gazebo + QGroundControl)
- World selector support
- Simulation and real mode separation at the UI / workflow level

---

## Architecture

The application is organized around a multi-layer architecture with separation between UI, state, services, integrations, and mission logic:

```text
command_center/
├── app/
│   ├── ui/               # PySide6 views and panels
│   ├── controllers/      # Application controllers
│   ├── services/         # SimController, process management
│   ├── events/           # EventBus (Qt signals)
│   └── state/            # StateStore (single source of truth)
├── integrations/
│   ├── mavsdk/           # DroneConnector, TelemetryManager
│   ├── mavlink/
│   ├── sim/
│   └── real/
├── mission/
│   ├── planning/         # Lawnmower pattern generation, mission upload
│   ├── execution/        # LawnmowerExecutor, UploadedMissionRunner
│   ├── validation/
│   └── autonomy/
├── system/
│   ├── process_manager/
│   ├── health_checks/
│   └── logging/
└── main.py
```

**Communication pattern:** event-driven via Qt signals. The UI emits commands, controllers call services, services publish events, and the UI subscribes to state updates.

**Key design decisions:**
- Telemetry is published to the UI at a controlled rate to avoid flooding the interface with high-frequency updates
- asyncio runs on a dedicated background thread so MAVSDK work does not block the Qt event loop
- SIM and REAL modes are designed around shared interfaces so the rest of the application can remain largely mode-agnostic
- Mission waypoints are generated and uploaded through a mission-planning pipeline intended to support both simulation and future hardware workflows

---

## Tech Stack

| Layer | Technology |
|---|---|
| Desktop UI | Python, PySide6 / Qt |
| Map / Mission Planner | Leaflet.js, OpenStreetMap |
| Vehicle Communication | MAVSDK, MAVLink |
| Simulation | PX4 SITL, Gazebo |
| Event System | Qt Signals (EventBus pattern) |
| State Management | Custom StateStore (QObject) |
| Configuration | JSON, YAML |
| Version Control | Git |

---

## Prerequisites

- Ubuntu 22.04 or later
- Python 3.10+
- PX4 Autopilot (built for SITL)
- Gazebo Garden or later
- QGroundControl (optional)

---

## Installation

```bash
# Clone the repository
git clone https://github.com/Colbymitchell1/drone-command-center.git
cd drone-command-center

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install PySide6 mavsdk PySide6-WebEngine

# Run
cd command_center
python main.py
```

---

## Usage

### Simulation Mode

1. Select **SIM** mode on launch
2. Choose a Gazebo world from the dropdown
3. Click **Start Sim Stack**
4. Wait for PX4 to initialize
5. Click **Connect** on the configured UDP port
6. Navigate to the **Mission Planner** tab
7. Click **Draw Search Area** and define your polygon on the map
8. Adjust leg spacing as needed
9. Click **Upload Mission**
10. Return to the main / overview tab
11. Click **Start Uploaded Mission**
12. Monitor telemetry and mission status
13. Click **Abort** at any time to trigger return-to-launch behavior

### Manual Lawnmower Mode

Use **Start Lawnmower Search** from the main interface for a more direct search workflow without the uploaded mission sequence.

---

## Roadmap

### Near-Term
- [ ] Fix mission coordinate / home reference mismatch
- [ ] Improve mission execution reliability
- [ ] Clean up mission planning workflow
- [ ] Improve UI clarity and operator flow

### Longer-Term
- [ ] Real hardware integration with flight controller / serial MAVLink workflow
- [ ] Video feed integration in the operator interface
- [ ] Mission feasibility checks (battery, time, altitude, basic constraints)
- [ ] AI mission assist / operator support features
- [ ] Multi-drone expansion and command-center style scaling

---

## Why I Built This

I wanted a project that was closer to the kind of work I’m genuinely interested in: autonomy, mission systems, simulation, and multi-component technical integration.

Rather than build something purely academic, I wanted something that forced me to deal with desktop UI architecture, simulator integration, telemetry pipelines, mission planning logic, and debugging across multiple moving parts.

This project is helping me bridge the gap between software, robotics, and real-world drone operations.

---

## Background

This project reflects the direction I’m intentionally moving toward technically.

My background is in aerospace maintenance, test environments, and hands-on troubleshooting. I’m now building more software and autonomy-focused projects to move deeper into robotics, drones, and integrated mission systems.

This is not meant to be presented as a finished product. It is meant to show real progress, real integration work, and real problem-solving in a domain I care about.

---

## Author

**Colby Mitchell**  
Systems Integration & Technical Operations | Autonomous Systems | Aerospace R&D  
Active Secret Clearance  

[GitHub](https://github.com/Colbymitchell1) · [LinkedIn](https://www.linkedin.com/in/colby-mitchell-804847128/)

---

## License

MIT License — see [LICENSE](LICENSE) for details.
