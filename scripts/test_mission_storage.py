"""
scripts/test_mission_storage.py

Round-trip test: create a sample MissionPlan and FlightLog,
save to disk, reload, and verify data integrity.

Run from the project root:
    python3 scripts/test_mission_storage.py
"""

import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.models.domain import (
    FailsafeConfig,
    FlightLog,
    FlightLogExecution,
    FlightLogPreflight,
    FlightLogRegulatory,
    LLMFinding,
    MissionGeometry,
    MissionPlan,
    MissionRiskReview,
    MissionType,
    OperatorInfo,
    RegulatoryContext,
    RiskLevel,
    Waypoint,
)
from app.services.mission_storage import MissionStorage


def build_sample_plan() -> MissionPlan:
    return MissionPlan(
        mission_type=MissionType.INSPECTION,
        site_name="Murrieta Test Site Alpha",
        notes="Rooftop inspection -- 3 passes at 25m AGL",
        geometry=MissionGeometry(
            waypoints=[
                Waypoint(lat=33.5731, lon=-117.2148, alt_m=25.0, index=0),
                Waypoint(lat=33.5741, lon=-117.2148, alt_m=25.0, index=1),
                Waypoint(lat=33.5741, lon=-117.2138, alt_m=25.0, index=2),
                Waypoint(lat=33.5731, lon=-117.2138, alt_m=25.0, index=3),
            ],
            geofence_polygon=[
                (33.5725, -117.2155),
                (33.5748, -117.2155),
                (33.5748, -117.2130),
                (33.5725, -117.2130),
            ],
            takeoff_location=(33.5731, -117.2148),
        ),
        regulatory=RegulatoryContext(
            night_operation=False,
            over_people=False,
            bvlos=False,
            max_altitude_agl_ft=400.0,
        ),
        failsafes=FailsafeConfig(),
        risk_review=MissionRiskReview(
            risk_level=RiskLevel.LOW,
            llm_model="ollama:llama3.1",
            plain_english_summary="No blockers. Operator should verify airspace class.",
            warnings=[
                LLMFinding(category="airspace", message="Airspace class not confirmed in mission data.")
            ],
            operator_acknowledged=True,
            reviewed_at_utc=datetime.now(timezone.utc),
        ),
    )


def build_sample_log(mission_id: str) -> FlightLog:
    return FlightLog(
        mission_id=mission_id,
        started_at_utc=datetime.now(timezone.utc),
        ended_at_utc=datetime.now(timezone.utc),
        operator=OperatorInfo(name_or_id="colby", part_107_confirmed=True),
        vehicle_id="drone-1",
        faa_registration_number="FA3XXXXXXX",
        remote_id_status="confirmed",
        adapter_type="MAVSDKVehicleAdapter",
        regulatory=FlightLogRegulatory(
            visual_line_of_sight_confirmed=True,
            max_altitude_agl_ft=82.0,
            night_operation=False,
        ),
        preflight=FlightLogPreflight(
            battery_percent=91.0,
            gps_fix="FIX_3D",
            home_position_set=True,
            airspace_checked=True,
            remote_id_checked=True,
            vehicle_inspection_completed=True,
            operator_acknowledged_risk_review=True,
        ),
        execution=FlightLogExecution(
            takeoff_location=(33.5731, -117.2148),
            landing_location=(33.5731, -117.2148),
            max_altitude_agl_ft=82.0,
            max_distance_from_home_m=143.0,
            flight_time_seconds=312.0,
            mission_completed=True,
        ),
        events=[
            {"type": "ARM",     "timestamp_utc": datetime.now(timezone.utc).isoformat()},
            {"type": "TAKEOFF", "timestamp_utc": datetime.now(timezone.utc).isoformat()},
            {"type": "LAND",    "timestamp_utc": datetime.now(timezone.utc).isoformat()},
            {"type": "DISARM",  "timestamp_utc": datetime.now(timezone.utc).isoformat()},
        ],
    )


def run_test():
    with tempfile.TemporaryDirectory() as tmp:
        storage = MissionStorage(base_dir=Path(tmp) / "missions")

        # --- MissionPlan round-trip ---
        plan = build_sample_plan()
        mission_dir = storage.save_mission(plan)
        print(f"✓  Mission saved: {mission_dir}")

        loaded_plan = storage.load_mission(mission_dir)
        assert loaded_plan.mission_id == plan.mission_id, "mission_id mismatch"
        assert loaded_plan.site_name == plan.site_name, "site_name mismatch"
        assert len(loaded_plan.geometry.waypoints) == 4, "waypoint count mismatch"
        assert loaded_plan.geometry.waypoints[2].lat == 33.5741, "waypoint lat mismatch"
        assert loaded_plan.risk_review is not None, "risk_review lost"
        assert loaded_plan.risk_review.operator_acknowledged is True, "ack flag lost"
        assert len(loaded_plan.risk_review.warnings) == 1, "warnings lost"
        print(f"✓  Mission round-trip verified ({len(loaded_plan.geometry.waypoints)} waypoints)")

        # --- FlightLog round-trip ---
        log = build_sample_log(plan.mission_id)
        log_path = storage.save_flight_log(log)
        print(f"✓  Flight log saved: {log_path}")

        loaded_log = storage.load_flight_log(log_path)
        assert loaded_log.mission_id == plan.mission_id, "log mission_id mismatch"
        assert loaded_log.execution.mission_completed is True, "completed flag lost"
        assert loaded_log.execution.flight_time_seconds == 312.0, "flight time lost"
        assert loaded_log.preflight.battery_percent == 91.0, "battery lost"
        assert len(loaded_log.events) == 4, "events lost"
        print(f"✓  Flight log round-trip verified ({len(loaded_log.events)} events)")

        # --- List missions ---
        missions = storage.list_missions()
        assert len(missions) == 1, f"expected 1 mission dir, got {len(missions)}"
        print(f"✓  list_missions() returned {len(missions)} mission(s)")

        # --- Show saved JSON structure ---
        print(f"\n--- Saved folder layout ---")
        for f in sorted((Path(tmp) / "missions").rglob("*")):
            print(f"  {f.relative_to(tmp)}")

    print("\n✓  All round-trip tests passed.")


if __name__ == "__main__":
    run_test()
