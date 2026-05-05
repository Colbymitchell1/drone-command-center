"""
app/services/mission_storage.py

Persists MissionPlan and FlightLog objects to disk as human-readable JSON.

Folder layout:
    missions/
        {mission_id}/
            mission.json          -- MissionPlan (plan + LLM risk review)
            flight_log.json       -- FlightLog (execution record + LLM post-review)
            preflight_review.json -- standalone preflight check results (optional)
            postflight_review.json -- standalone LLM review (optional)

All timestamps are ISO 8601 UTC.
All files include a schema_version field for forward compatibility.

Usage:
    storage = MissionStorage(base_dir=Path("missions"))

    # Save
    mission_dir = storage.save_mission(plan)

    # Load
    plan = storage.load_mission(mission_dir / "mission.json")
    log  = storage.load_flight_log(mission_dir / "flight_log.json")
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.models.domain import (
    FailsafeConfig,
    FlightLog,
    FlightLogExecution,
    FlightLogPreflight,
    FlightLogRegulatory,
    GpsFixType,
    FlightMode,
    IncidentRecord,
    LLMFinding,
    MissionGeometry,
    MissionPlan,
    MissionRiskReview,
    MissionType,
    MissionValidationResult,
    OperatingRule,
    OperatorInfo,
    PostMissionReview,
    PreflightCheck,
    PreflightResult,
    RegulatoryContext,
    RiskLevel,
    CheckStatus,
    VehicleState,
    Waypoint,
)

log = logging.getLogger(__name__)

SCHEMA_VERSION = "0.1.0"
SUPPORTED_VERSIONS = {"0.1.0"}


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def _serialize_value(v: Any) -> Any:
    """Recursively convert domain types to JSON-safe primitives."""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.isoformat()
    if hasattr(v, "value") and hasattr(v, "__class__") and issubclass(v.__class__, str):
        return v.value  # StrEnum
    if hasattr(v, "__dataclass_fields__"):
        return {k: _serialize_value(getattr(v, k)) for k in v.__dataclass_fields__}
    if isinstance(v, list):
        return [_serialize_value(i) for i in v]
    if isinstance(v, tuple):
        return list(v)
    if isinstance(v, dict):
        return {k: _serialize_value(val) for k, val in v.items()}
    return v


def _to_dict(obj: Any) -> dict:
    return {k: _serialize_value(getattr(obj, k)) for k in obj.__dataclass_fields__}


# ---------------------------------------------------------------------------
# Deserialization helpers
# ---------------------------------------------------------------------------


def _dt(v: Optional[str]) -> Optional[datetime]:
    if not v:
        return None
    try:
        return datetime.fromisoformat(v)
    except (ValueError, TypeError):
        return None


def _waypoints(raw: list) -> list[Waypoint]:
    return [
        Waypoint(
            lat=w.get("lat", 0.0),
            lon=w.get("lon", 0.0),
            alt_m=w.get("alt_m", 25.0),
            speed_mps=w.get("speed_mps", 5.0),
            acceptance_radius=w.get("acceptance_radius", 2.0),
            is_fly_through=w.get("is_fly_through", True),
            index=w.get("index", 0),
        )
        for w in (raw or [])
    ]


def _geometry(raw: dict) -> MissionGeometry:
    return MissionGeometry(
        waypoints=_waypoints(raw.get("waypoints", [])),
        aoi_polygon=[tuple(p) for p in raw.get("aoi_polygon", [])],
        geofence_polygon=[tuple(p) for p in raw.get("geofence_polygon", [])],
        takeoff_location=tuple(raw["takeoff_location"]) if raw.get("takeoff_location") else None,
        landing_location=tuple(raw["landing_location"]) if raw.get("landing_location") else None,
    )


def _regulatory(raw: dict) -> RegulatoryContext:
    return RegulatoryContext(
        country=raw.get("country", "US"),
        operating_rule=OperatingRule(raw.get("operating_rule", "FAA_PART_107")),
        requires_controlled_airspace=raw.get("requires_controlled_airspace", False),
        night_operation=raw.get("night_operation", False),
        over_people=raw.get("over_people", False),
        over_moving_vehicles=raw.get("over_moving_vehicles", False),
        bvlos=raw.get("bvlos", False),
        max_altitude_agl_ft=raw.get("max_altitude_agl_ft", 400.0),
    )


def _failsafes(raw: dict) -> FailsafeConfig:
    return FailsafeConfig(
        lost_link_action=raw.get("lost_link_action", "RTL"),
        low_battery_action=raw.get("low_battery_action", "RTL"),
        geofence_breach_action=raw.get("geofence_breach_action", "RTL"),
        mission_abort_action=raw.get("mission_abort_action", "HOLD"),
    )


def _risk_review(raw: Optional[dict]) -> Optional[MissionRiskReview]:
    if not raw:
        return None
    return MissionRiskReview(
        risk_level=RiskLevel(raw.get("risk_level", "low")),
        llm_model=raw.get("llm_model", ""),
        blocking_items=raw.get("blocking_items", []),
        warnings=[LLMFinding(**w) for w in raw.get("warnings", [])],
        missing_information=raw.get("missing_information", []),
        regulatory_flags=raw.get("regulatory_flags", []),
        vehicle_readiness_flags=raw.get("vehicle_readiness_flags", []),
        airspace_weather_flags=raw.get("airspace_weather_flags", []),
        suggested_operator_questions=raw.get("suggested_operator_questions", []),
        plain_english_summary=raw.get("plain_english_summary", ""),
        operator_acknowledged=raw.get("operator_acknowledged", False),
        reviewed_at_utc=_dt(raw.get("reviewed_at_utc")),
    )


def _post_review(raw: Optional[dict]) -> Optional[PostMissionReview]:
    if not raw:
        return None
    return PostMissionReview(
        llm_model=raw.get("llm_model", ""),
        findings=[LLMFinding(**f) for f in raw.get("findings", [])],
        recommended_followups=raw.get("recommended_followups", []),
        plain_english_summary=raw.get("plain_english_summary", ""),
        reviewed_at_utc=_dt(raw.get("reviewed_at_utc")),
    )


# ---------------------------------------------------------------------------
# MissionStorage
# ---------------------------------------------------------------------------


class MissionStorage:
    """
    Saves and loads MissionPlan and FlightLog objects to/from disk.

    All I/O is synchronous -- call from a background thread if needed.
    """

    def __init__(self, base_dir: Path = Path("missions")):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    # ── Directory helpers ────────────────────────────────────────────────────

    def mission_dir(self, mission_id: str) -> Path:
        return self.base_dir / mission_id

    def _ensure_dir(self, mission_id: str) -> Path:
        d = self.mission_dir(mission_id)
        d.mkdir(parents=True, exist_ok=True)
        return d

    # ── MissionPlan ──────────────────────────────────────────────────────────

    def save_mission(self, plan: MissionPlan) -> Path:
        """
        Serialize MissionPlan to missions/{mission_id}/mission.json.
        Returns the path to the saved file.
        """
        d = self._ensure_dir(plan.mission_id)
        payload = {
            "schema_version": SCHEMA_VERSION,
            **_to_dict(plan),
        }
        path = d / "mission.json"
        path.write_text(json.dumps(payload, indent=2))
        log.info(f"Mission saved: {path}")
        return path

    def load_mission(self, path: Path) -> MissionPlan:
        """
        Load a MissionPlan from a mission.json file.
        Raises ValueError on schema version mismatch or corrupt JSON.
        """
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Mission file not found: {path}")

        try:
            raw = json.loads(path.read_text())
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in {path}: {e}") from e

        version = raw.get("schema_version", "unknown")
        if version not in SUPPORTED_VERSIONS:
            raise ValueError(
                f"Unsupported schema version '{version}' in {path}. "
                f"Supported: {SUPPORTED_VERSIONS}"
            )

        return MissionPlan(
            mission_id=raw.get("mission_id", ""),
            created_at_utc=_dt(raw.get("created_at_utc")) or datetime.now(timezone.utc),
            mission_type=MissionType(raw.get("mission_type", "other")),
            site_name=raw.get("site_name", ""),
            notes=raw.get("notes", ""),
            geometry=_geometry(raw.get("geometry", {})),
            regulatory=_regulatory(raw.get("regulatory", {})),
            failsafes=_failsafes(raw.get("failsafes", {})),
            risk_review=_risk_review(raw.get("risk_review")),
        )

    # ── FlightLog ────────────────────────────────────────────────────────────

    def save_flight_log(self, flight_log: FlightLog) -> Path:
        """
        Serialize FlightLog to missions/{mission_id}/flight_log.json.
        Telemetry track is included but can be large -- consider downsampling
        before saving if recording at high rates.
        Returns the path to the saved file.
        """
        d = self._ensure_dir(flight_log.mission_id)
        payload = {
            "schema_version": SCHEMA_VERSION,
            **_to_dict(flight_log),
        }
        path = d / "flight_log.json"
        path.write_text(json.dumps(payload, indent=2))
        log.info(f"Flight log saved: {path}")
        return path

    def load_flight_log(self, path: Path) -> FlightLog:
        """
        Load a FlightLog from a flight_log.json file.
        Raises ValueError on schema mismatch or corrupt JSON.
        """
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Flight log not found: {path}")

        try:
            raw = json.loads(path.read_text())
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in {path}: {e}") from e

        version = raw.get("schema_version", "unknown")
        if version not in SUPPORTED_VERSIONS:
            raise ValueError(f"Unsupported schema version '{version}' in {path}")

        reg = raw.get("regulatory", {})
        pre = raw.get("preflight", {})
        exe = raw.get("execution", {})
        inc = raw.get("incidents", {})
        op  = raw.get("operator", {})

        return FlightLog(
            flight_log_id=raw.get("flight_log_id", ""),
            mission_id=raw.get("mission_id", ""),
            started_at_utc=_dt(raw.get("started_at_utc")),
            ended_at_utc=_dt(raw.get("ended_at_utc")),
            operator=OperatorInfo(
                name_or_id=op.get("name_or_id", "unknown"),
                part_107_confirmed=op.get("part_107_confirmed", False),
            ),
            vehicle_id=raw.get("vehicle_id", ""),
            faa_registration_number=raw.get("faa_registration_number", ""),
            remote_id_status=raw.get("remote_id_status", "unknown"),
            adapter_type=raw.get("adapter_type", ""),
            regulatory=FlightLogRegulatory(
                operating_rule=OperatingRule(reg.get("operating_rule", "FAA_PART_107")),
                visual_line_of_sight_confirmed=reg.get("visual_line_of_sight_confirmed", False),
                max_altitude_agl_ft=reg.get("max_altitude_agl_ft", 0.0),
                controlled_airspace_auth_required=reg.get("controlled_airspace_auth_required", False),
                controlled_airspace_auth_id=reg.get("controlled_airspace_auth_id", ""),
                night_operation=reg.get("night_operation", False),
                anti_collision_lighting_confirmed=reg.get("anti_collision_lighting_confirmed", False),
                operation_over_people=reg.get("operation_over_people", False),
                operation_over_moving_vehicles=reg.get("operation_over_moving_vehicles", False),
            ),
            preflight=FlightLogPreflight(
                battery_percent=pre.get("battery_percent"),
                gps_fix=pre.get("gps_fix", ""),
                home_position_set=pre.get("home_position_set", False),
                airspace_checked=pre.get("airspace_checked", False),
                remote_id_checked=pre.get("remote_id_checked", False),
                vehicle_inspection_completed=pre.get("vehicle_inspection_completed", False),
                operator_acknowledged_risk_review=pre.get("operator_acknowledged_risk_review", False),
            ),
            execution=FlightLogExecution(
                takeoff_location=tuple(exe["takeoff_location"]) if exe.get("takeoff_location") else None,
                landing_location=tuple(exe["landing_location"]) if exe.get("landing_location") else None,
                max_altitude_agl_ft=exe.get("max_altitude_agl_ft", 0.0),
                max_distance_from_home_m=exe.get("max_distance_from_home_m", 0.0),
                flight_time_seconds=exe.get("flight_time_seconds", 0.0),
                mission_completed=exe.get("mission_completed", False),
                abort_reason=exe.get("abort_reason"),
            ),
            incidents=IncidentRecord(
                accident_report_required=inc.get("accident_report_required", False),
                injury_or_loss_of_consciousness=inc.get("injury_or_loss_of_consciousness", False),
                property_damage_over_500=inc.get("property_damage_over_500", False),
                notes=inc.get("notes", ""),
            ),
            telemetry_track=[],  # not rehydrated -- too large; kept in file for archival
            events=raw.get("events", []),
            postflight_review=_post_review(raw.get("postflight_review")),
        )

    # ── Listing ──────────────────────────────────────────────────────────────

    def list_missions(self) -> list[Path]:
        """Return sorted list of mission directories."""
        return sorted(
            [d for d in self.base_dir.iterdir() if d.is_dir()],
            key=lambda d: d.stat().st_mtime,
            reverse=True,
        )
