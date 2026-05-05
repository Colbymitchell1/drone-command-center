"""
app/models/domain.py

Core domain models for the Drone Command Center.

These are the canonical data types used throughout the application.
No MAVSDK types, no PySide6 types, no transport types leak into this module.
Everything in the system speaks this language.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class FlightMode(str, Enum):
    """Normalized flight mode -- adapter maps vehicle-specific modes to these."""
    UNKNOWN       = "UNKNOWN"
    IDLE          = "IDLE"
    MANUAL        = "MANUAL"
    STABILIZED    = "STABILIZED"
    MISSION       = "MISSION"
    RTL           = "RTL"
    LAND          = "LAND"
    HOLD          = "HOLD"
    OFFBOARD      = "OFFBOARD"
    TAKEOFF       = "TAKEOFF"
    EMERGENCY     = "EMERGENCY"


class GpsFixType(str, Enum):
    """Normalized GPS fix quality."""
    NO_GPS      = "NO_GPS"
    NO_FIX      = "NO_FIX"
    FIX_2D      = "FIX_2D"
    FIX_3D      = "FIX_3D"
    FIX_DGPS    = "FIX_DGPS"
    RTK_FLOAT   = "RTK_FLOAT"
    RTK_FIXED   = "RTK_FIXED"


class MissionStatus(str, Enum):
    """Lifecycle state of a mission execution."""
    IDLE        = "IDLE"
    UPLOADING   = "UPLOADING"
    UPLOADED    = "UPLOADED"
    RUNNING     = "RUNNING"
    PAUSED      = "PAUSED"
    COMPLETE    = "COMPLETE"
    ABORTED     = "ABORTED"
    FAILED      = "FAILED"


class CommandStatus(str, Enum):
    """Result of a command sent to the vehicle."""
    SUCCESS     = "SUCCESS"
    FAILED      = "FAILED"
    TIMEOUT     = "TIMEOUT"
    REJECTED    = "REJECTED"
    UNSUPPORTED = "UNSUPPORTED"


class CheckStatus(str, Enum):
    """Result of a single preflight check."""
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"


class RiskLevel(str, Enum):
    """LLM-assigned mission risk level."""
    LOW     = "low"
    MEDIUM  = "medium"
    HIGH    = "high"
    BLOCKED = "blocked"


class MissionType(str, Enum):
    """Civilian mission categories."""
    INSPECTION      = "inspection"
    SURVEY          = "survey"
    AGRICULTURE     = "agriculture"
    SAR             = "sar"
    FIRE_ASSESSMENT = "fire_assessment"
    TRAINING        = "training"
    OTHER           = "other"


class OperatingRule(str, Enum):
    FAA_PART_107 = "FAA_PART_107"


# ---------------------------------------------------------------------------
# Vehicle identity and capabilities
# ---------------------------------------------------------------------------


@dataclass
class VehicleIdentity:
    """Static identity information about a connected vehicle."""
    vehicle_id:    str
    adapter_type:  str            # e.g. "MAVSDKVehicleAdapter"
    firmware:      str = "unknown"
    vehicle_type:  str = "unknown"
    autopilot:     str = "unknown"


@dataclass
class VehicleCapabilities:
    """
    Feature flags for what this vehicle/adapter combination supports.
    The rest of the system checks these before calling optional methods.
    """
    supports_mission_upload:    bool = True
    supports_geofence:          bool = False
    supports_rtl:               bool = True
    supports_pause_resume:      bool = False
    supports_guided_goto:       bool = False
    supports_camera_trigger:    bool = False
    supports_battery_status:    bool = True
    supports_health_checks:     bool = True
    supports_log_download:      bool = False
    supports_remote_id_status:  bool = False
    supports_offboard_velocity: bool = False


# ---------------------------------------------------------------------------
# Vehicle state (telemetry snapshot)
# ---------------------------------------------------------------------------


@dataclass
class VehicleState:
    """
    Normalized telemetry snapshot emitted by the adapter at a fixed rate.
    All fields are Optional -- partial updates are valid.
    """
    vehicle_id:             str
    timestamp_utc:          datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # Position
    lat:                    Optional[float] = None   # degrees
    lon:                    Optional[float] = None   # degrees
    alt_amsl:               Optional[float] = None   # metres above mean sea level
    alt_relative:           Optional[float] = None   # metres above home (AGL)

    # Motion
    heading_deg:            Optional[float] = None
    ground_speed_mps:       Optional[float] = None
    vertical_speed_mps:     Optional[float] = None

    # Power
    battery_percent:        Optional[float] = None   # 0.0 – 100.0; None = unknown
    battery_voltage:        Optional[float] = None   # volts
    is_sim_battery:         bool = False              # True when SITL returns sentinel

    # Status
    flight_mode:            FlightMode = FlightMode.UNKNOWN
    armed:                  Optional[bool] = None
    in_air:                 Optional[bool] = None

    # GPS
    gps_fix_type:           GpsFixType = GpsFixType.NO_FIX
    satellites:             Optional[int] = None

    # Mission progress
    current_mission_item:   Optional[int] = None

    # Link / health
    link_quality:           Optional[float] = None   # 0.0 – 1.0
    failsafe_active:        bool = False


# ---------------------------------------------------------------------------
# Vehicle health
# ---------------------------------------------------------------------------


@dataclass
class VehicleHealth:
    """
    Structured health / readiness report returned by the adapter.
    Used for the preflight check flow -- UI shows blockers before arming.
    """
    ready:          bool
    blocking:       list[str] = field(default_factory=list)   # hard failures
    warnings:       list[str] = field(default_factory=list)   # soft advisories

    def can_arm(self) -> bool:
        return self.ready and not self.blocking


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@dataclass
class CommandResult:
    """
    Structured result returned by every adapter command method.
    Never raise on command failure -- return a CommandResult with status=FAILED.
    """
    status:      CommandStatus
    message:     str = ""
    code:        str = ""          # e.g. "MISSION_UPLOAD_TIMEOUT"
    recoverable: bool = True

    @property
    def ok(self) -> bool:
        return self.status == CommandStatus.SUCCESS

    @classmethod
    def success(cls, message: str = "") -> "CommandResult":
        return cls(status=CommandStatus.SUCCESS, message=message)

    @classmethod
    def failed(cls, message: str, code: str = "", recoverable: bool = True) -> "CommandResult":
        return cls(
            status=CommandStatus.FAILED,
            message=message,
            code=code,
            recoverable=recoverable,
        )

    @classmethod
    def unsupported(cls, method: str) -> "CommandResult":
        return cls(
            status=CommandStatus.UNSUPPORTED,
            message=f"{method} is not supported by this adapter",
            recoverable=False,
        )


# ---------------------------------------------------------------------------
# Mission planning
# ---------------------------------------------------------------------------


@dataclass
class Waypoint:
    """A single mission waypoint."""
    lat:               float
    lon:               float
    alt_m:             float = 25.0       # relative altitude AGL
    speed_mps:         float = 5.0
    acceptance_radius: float = 2.0
    is_fly_through:    bool  = True
    index:             int   = 0


@dataclass
class MissionGeometry:
    """Spatial definition of a mission -- polygon AOI, geofence, takeoff/landing."""
    waypoints:          list[Waypoint] = field(default_factory=list)
    aoi_polygon:        list[tuple[float, float]] = field(default_factory=list)
    geofence_polygon:   list[tuple[float, float]] = field(default_factory=list)
    takeoff_location:   Optional[tuple[float, float]] = None
    landing_location:   Optional[tuple[float, float]] = None


@dataclass
class RegulatoryContext:
    """Part 107 operational flags for this mission."""
    country:                        str = "US"
    operating_rule:                 OperatingRule = OperatingRule.FAA_PART_107
    requires_controlled_airspace:   bool = False
    night_operation:                bool = False
    over_people:                    bool = False
    over_moving_vehicles:           bool = False
    bvlos:                          bool = False
    max_altitude_agl_ft:            float = 400.0


@dataclass
class FailsafeConfig:
    """What the vehicle should do when a failure condition is detected."""
    lost_link_action:       str = "RTL"
    low_battery_action:     str = "RTL"
    geofence_breach_action: str = "RTL"
    mission_abort_action:   str = "HOLD"


# ---------------------------------------------------------------------------
# LLM review structures
# ---------------------------------------------------------------------------


@dataclass
class LLMFinding:
    category: str    # e.g. "battery", "airspace", "regulatory"
    message:  str


@dataclass
class MissionRiskReview:
    """Pre-mission LLM risk review output."""
    risk_level:                  RiskLevel = RiskLevel.LOW
    llm_model:                   str = ""
    blocking_items:              list[str] = field(default_factory=list)
    warnings:                    list[LLMFinding] = field(default_factory=list)
    missing_information:         list[str] = field(default_factory=list)
    regulatory_flags:            list[str] = field(default_factory=list)
    vehicle_readiness_flags:     list[str] = field(default_factory=list)
    airspace_weather_flags:      list[str] = field(default_factory=list)
    suggested_operator_questions: list[str] = field(default_factory=list)
    plain_english_summary:       str = ""
    operator_acknowledged:       bool = False
    reviewed_at_utc:             Optional[datetime] = None

    @property
    def is_blocked(self) -> bool:
        return self.risk_level == RiskLevel.BLOCKED or bool(self.blocking_items)


@dataclass
class PostMissionReview:
    """Post-mission LLM analysis output."""
    llm_model:              str = ""
    findings:               list[LLMFinding] = field(default_factory=list)
    recommended_followups:  list[str] = field(default_factory=list)
    plain_english_summary:  str = ""
    reviewed_at_utc:        Optional[datetime] = None


# ---------------------------------------------------------------------------
# Mission plan (the full pre-flight document)
# ---------------------------------------------------------------------------


@dataclass
class MissionPlan:
    """
    Complete mission plan document.
    This is the input to both the LLM review and the vehicle adapter.
    Serializes to/from JSON as the canonical mission file format.
    """
    mission_id:         str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at_utc:     datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    mission_type:       MissionType = MissionType.OTHER
    site_name:          str = ""
    notes:              str = ""

    geometry:           MissionGeometry = field(default_factory=MissionGeometry)
    regulatory:         RegulatoryContext = field(default_factory=RegulatoryContext)
    failsafes:          FailsafeConfig = field(default_factory=FailsafeConfig)

    risk_review:        Optional[MissionRiskReview] = None


# ---------------------------------------------------------------------------
# Preflight checks (deterministic, no LLM)
# ---------------------------------------------------------------------------


@dataclass
class PreflightCheck:
    name:    str
    status:  CheckStatus
    message: str


@dataclass
class PreflightResult:
    """Result of the deterministic preflight check suite."""
    checks:                     list[PreflightCheck] = field(default_factory=list)
    estimated_flight_time_min:  float = 0.0
    estimated_battery_drain_pct: float = 0.0
    required_battery_pct:       float = 0.0
    available_battery_pct:      float = 0.0
    is_sim_battery:             bool = False

    @property
    def can_launch(self) -> bool:
        return all(c.status != CheckStatus.FAIL for c in self.checks)


# ---------------------------------------------------------------------------
# Mission validation
# ---------------------------------------------------------------------------


@dataclass
class MissionValidationResult:
    """
    Result of adapter.validate_mission() -- pure logic check before upload.
    Separate from preflight (which requires a live vehicle connection).
    """
    valid:    bool
    errors:   list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Flight log (the full post-flight record)
# ---------------------------------------------------------------------------


@dataclass
class OperatorInfo:
    name_or_id: str = "unknown"
    part_107_confirmed: bool = False


@dataclass
class FlightLogRegulatory:
    """Part 107 compliance fields recorded at flight time."""
    operating_rule:                     OperatingRule = OperatingRule.FAA_PART_107
    visual_line_of_sight_confirmed:     bool = False
    max_altitude_agl_ft:                float = 0.0
    controlled_airspace_auth_required:  bool = False
    controlled_airspace_auth_id:        str = ""
    night_operation:                    bool = False
    anti_collision_lighting_confirmed:  bool = False
    operation_over_people:              bool = False
    operation_over_moving_vehicles:     bool = False


@dataclass
class FlightLogPreflight:
    """Snapshot of preflight state at mission start."""
    battery_percent:                    Optional[float] = None
    gps_fix:                            str = ""
    home_position_set:                  bool = False
    airspace_checked:                   bool = False
    remote_id_checked:                  bool = False
    vehicle_inspection_completed:       bool = False
    operator_acknowledged_risk_review:  bool = False


@dataclass
class FlightLogExecution:
    """Summary of what actually happened during the flight."""
    takeoff_location:       Optional[tuple[float, float]] = None
    landing_location:       Optional[tuple[float, float]] = None
    max_altitude_agl_ft:    float = 0.0
    max_distance_from_home_m: float = 0.0
    flight_time_seconds:    float = 0.0
    mission_completed:      bool = False
    abort_reason:           Optional[str] = None


@dataclass
class IncidentRecord:
    """FAA-reportable incident tracking (Part 107 -- report within 10 days)."""
    accident_report_required:       bool = False
    injury_or_loss_of_consciousness: bool = False
    property_damage_over_500:       bool = False
    notes:                          str = ""


@dataclass
class FlightLog:
    """
    Complete post-flight record.
    One FlightLog per executed mission. The mission_id links it to the MissionPlan.
    This is the document that supports Part 107 operational discipline.
    """
    flight_log_id:  str = field(default_factory=lambda: str(uuid.uuid4()))
    mission_id:     str = ""
    started_at_utc: Optional[datetime] = None
    ended_at_utc:   Optional[datetime] = None

    operator:       OperatorInfo = field(default_factory=OperatorInfo)
    vehicle_id:     str = ""
    faa_registration_number: str = ""
    remote_id_status: str = "unknown"   # "confirmed" | "not_confirmed" | "unknown"
    adapter_type:   str = ""

    regulatory:     FlightLogRegulatory = field(default_factory=FlightLogRegulatory)
    preflight:      FlightLogPreflight = field(default_factory=FlightLogPreflight)
    execution:      FlightLogExecution = field(default_factory=FlightLogExecution)
    incidents:      IncidentRecord = field(default_factory=IncidentRecord)

    # Telemetry snapshots recorded during the flight
    telemetry_track: list[VehicleState] = field(default_factory=list)

    # Timestamped event log (arm, disarm, mode changes, warnings, aborts)
    events: list[dict] = field(default_factory=list)

    # LLM post-mission review (populated after landing)
    postflight_review: Optional[PostMissionReview] = None
