# Autonomous Drone Command Center

A desktop ground station for autonomous drone mission planning, execution, telemetry monitoring, and AI-assisted mission review.

![Mission Planner](docs/mission_planner.png)

---

## Overview

This is a full-stack PySide6 desktop application built to support autonomous drone operations workflows in simulation, with a hardware-ready architecture. It integrates with PX4 SITL, Gazebo, and MAVSDK to provide an operator-facing interface for mission planning, execution, telemetry monitoring, and safety control.

The system is built around a provider-agnostic LLM review pipeline that performs pre-mission safety analysis and post-mission reporting. Operators can use a local model via Ollama or a cloud provider (Claude, OpenAI, Gemini) through a single abstraction layer — no vendor lock-in.

This is an active portfolio project. The architecture is designed for real-world field use: reliable, extensible, and simulation-proven before hardware integration.

---

## Demo

### Screen Recording
https://www.linkedin.com/posts/colby-mitchell-804847128_dronetech-autonomy-uav-ugcPost-7441303931038883840-cZa1?utm_source=share&utm_medium=member_desktop&rcm=ACoAAB9y18QBXRWFyq6ecRSKmHlAtFM-7MV-BZw

Suggested demo flow:
- Application startup
- Simulation stack launch
- UDP vehicle connection
- Mission area selection on the map
- Mission upload and execution
- LLM pre-mission review
- Live telemetry monitoring in Gazebo

### Screenshots
<img width="982" height="700" alt="Screenshot from 2026-03-21 17-40-47" src="https://github.com/user-attachments/assets/d520a6cf-953d-444b-864e-7005c99d05b9" />
<img width="982" height="700" alt="Pasted image" src="https://github.com/user-attachments/assets/1405702a-3c0f-4405-9925-7139fc7c7c58" />

---

## Current Status

The project is functional in simulation and demonstrates the full mission loop end-to-end:

1. Launch command center
2. Start simulation stack (PX4 SITL + Gazebo)
3. Connect to vehicle over UDP
4. Draw mission area on the 3D map
5. Generate lawnmower coverage pattern
6. Submit mission for LLM pre-mission review
7. Upload and execute mission
8. Monitor live telemetry throughout
9. Review post-mission summary

---

## Features

### Mission Planning
- Interactive 3D map interface (CesiumJS) with real-world coordinates and terrain rendering
- Polygon-based area of interest (AOI) definition — operator draws the boundary, system generates the pattern
- Box mode for rapid rectangular area selection
- Automatic lawnmower coverage pattern generation fitted to the polygon
- Adjustable leg spacing for coverage density control
- Mission upload to vehicle via MAVSDK

### Mission Execution
- Uploaded mission execution via PX4 mission API
- Manual lawnmower search mode with configurable parameters
- Real-time mission status: IDLE / RUNNING / COMPLETE / ABORTED
- Operator abort with immediate RTL and automatic landing behavior
- Post-mission RTL with retry handling
- Safety-oriented mission stop workflow

### LLM Mission Review Pipeline
- Pre-mission safety analysis before execution — flags constraint violations, battery estimates, altitude conflicts
- Post-mission summary generation with performance notes
- Provider-agnostic LLM backend: Claude, OpenAI, Gemini (BYOK), or local Ollama
- Single abstraction layer — swap providers without touching mission logic
- Local inference supported via Ollama over Tailscale for air-gapped / offline operation

### Telemetry & System Health
- Live telemetry: latitude, longitude, altitude, speed, heading, battery
- System health visibility for PX4 SITL, Gazebo, and UDP connectivity
- Vehicle connection status with connect/disconnect control

### Simulation Management
- One-click sim stack launch (PX4 SITL + Gazebo + QGroundControl)
- World selector support
- Clean SIM / REAL mode separation at the UI and workflow level

---

## Architecture

The application is organized around a multi-layer architecture with clear separation between UI, state, services, integrations, and mission logic.

```text
command_center/
├── app/
│   ├── ui/               # PySide6 views and panels
│   ├── controllers/      # Application controllers
│   ├── services/         # SimController, process management
│   ├── events/           # EventBus (Qt signals)
│   └── state/            # StateStore (single source of truth)
├── integrations/
│   ├── mavsdk/           # VehicleAdapter, TelemetryManager
│   ├── mavlink/
│   ├── llm/              # Provider-agnostic LLM abstraction layer
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

**Communication pattern:** Event-driven via Qt signals (EventBus). The UI emits commands, controllers call services, services publish events, and the UI subscribes to state updates. No direct coupling between layers.

**Key design decisions:**
- VehicleAdapter abstracts the vehicle interface so SIM and REAL modes share the same command surface
- Telemetry is published to the UI at a controlled rate to avoid flooding the interface with high-frequency updates
- asyncio runs on a dedicated background thread so MAVSDK work does not block the Qt event loop
- LLM provider is runtime-configurable — the mission pipeline calls one interface regardless of backend
- Mission waypoints flow through a planning pipeline designed for both simulation and future hardware use

---

## Tech Stack

| Layer | Technology |
|---|---|
| Desktop UI | Python, PySide6 / Qt |
| Map / Mission Planner | CesiumJS (3D terrain, real-world coordinates) |
| Vehicle Communication | MAVSDK, MAVLink |
| Vehicle Abstraction | VehicleAdapter (SIM/REAL interface) |
| LLM Review Pipeline | Claude / OpenAI / Gemini / Ollama (provider-agnostic) |
| Simulation | PX4 SITL, Gazebo |
| Event System | Qt Signals (EventBus pattern) |
| State Management | Custom StateStore (QObject) |
| Remote Inference | Ollama over Tailscale |
| Configuration | JSON, YAML |
| Version Control | Git |

---

## Prerequisites

- Ubuntu 22.04 or later
- Python 3.10+
- PX4 Autopilot (built for SITL)
- Gazebo Garden or later
- QGroundControl (optional)
- Ollama (optional, for local LLM inference)

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
10. Review the LLM pre-mission analysis
11. Return to the main tab and click **Start Uploaded Mission**
12. Monitor telemetry and mission status
13. Click **Abort** at any time to trigger RTL behavior

### Manual Lawnmower Mode

Use **Start Lawnmower Search** from the main interface for direct search execution without the uploaded mission sequence.

### LLM Configuration

Set your preferred provider and API key in the settings panel before mission upload. For local inference, point the Ollama endpoint at your local or Tailscale-connected server.

---

## Roadmap

### Near-Term
- [ ] Wire LLM pre-mission review panel into UI
- [ ] Post-mission review display in UI
- [ ] Mission save / load from planning view
- [ ] Hotkeys for emergency stop, mission pause, and resume

### Medium-Term
- [ ] FPV video feed panel (GStreamer / UDP stream from companion computer)
- [ ] Gesture-based operator input via base station camera (MediaPipe)
- [ ] Real hardware integration (serial MAVLink, Pixhawk/Cube)

### Longer-Term
- [ ] Onboard intelligence integration (Jetson Orin, obstacle avoidance, onboard recording)
- [ ] Multi-drone command and control
- [ ] ATAK / CoT integration for multi-operator coordination
- [ ] AI natural language command interface

---

## Why I Built This

I wanted a project that dealt with the kind of problems that actually show up in autonomous systems work: multi-component integration, real-time state management, simulation-to-hardware workflows, and operator-facing interfaces that have to be reliable under pressure.

This isn't a tutorial project. It's a working system built to prove architecture, not just syntax.

---

## Background

My background is in aerospace maintenance and test environments across military and commercial domains. I'm building deeper into software, autonomy, and mission systems -- this project is the practical application of that direction.

The goal was a system I could demo, extend, and eventually fly on real hardware. That's still the plan.

---

## Author

**Colby Mitchell**
Systems Integration & Technical Operations | Autonomous Systems | Aerospace R&D
Active Secret Clearance

[GitHub](https://github.com/Colbymitchell1) · [LinkedIn](https://www.linkedin.com/in/colby-mitchell-804847128/)

---

## License

MIT License — see [LICENSE](LICENSE) for details.
