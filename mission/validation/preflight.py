"""
Pre-flight validation for drone missions.

All safety calculations are deterministic math only — no AI inference in the
control path.  Constants at the top of this module are designed to be tuned
as the platform matures without touching any logic.
"""

import asyncio
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Tuple

from mavsdk import System

from app.events.event_bus import bus

# ── Tunable constants ─────────────────────────────────────────────────────────

CRUISE_SPEED_M_S          = 5.0   # m/s  — matches uploader.py speed_m_s
BATTERY_DRAIN_PCT_PER_MIN = 2.0   # %/min — conservative flat-rate estimate
RTL_RESERVE_PCT           = 15.0  # %    — held in reserve for the RTL leg
SAFETY_MARGIN_PCT         = 20.0  # %    — additional buffer on top of RTL reserve
TIGHT_MARGIN_PCT          = 10.0  # %    — WARN if (available − required) < this

GPS_TIMEOUT_S             = 5.0   # seconds to wait for a single telemetry read
HOME_TIMEOUT_S            = 5.0
BATTERY_TIMEOUT_S         = 5.0

# MAVSDK FixType int thresholds
_MIN_FIX_INT = 3   # FIX_3D and above are acceptable

# ── Types ─────────────────────────────────────────────────────────────────────

OffsetM = Tuple[float, float]   # (north_m, east_m)


class CheckStatus(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"


@dataclass
class PreflightCheck:
    name: str
    status: CheckStatus
    message: str


@dataclass
class PreflightResult:
    checks: List[PreflightCheck] = field(default_factory=list)
    estimated_flight_time_min: float = 0.0
    estimated_battery_pct: float = 0.0    # drain from mission flight alone
    required_battery_pct: float = 0.0    # drain + RTL reserve + safety margin
    available_battery_pct: float = 0.0   # current battery reading (−1 = SIM)
    is_sim_battery: bool = False          # True when simulator returns −1 sentinel

    @property
    def can_launch(self) -> bool:
        """True when no check has a FAIL status."""
        return all(c.status != CheckStatus.FAIL for c in self.checks)


# ── Internal helpers ──────────────────────────────────────────────────────────

def _mission_distance_m(offsets: List[OffsetM]) -> float:
    """Sum of straight-line distances between consecutive (north_m, east_m) pairs."""
    total = 0.0
    for i in range(1, len(offsets)):
        dn = offsets[i][0] - offsets[i - 1][0]
        de = offsets[i][1] - offsets[i - 1][1]
        total += math.sqrt(dn * dn + de * de)
    return total


async def _first(async_gen, timeout: float):
    """Return the first item from an async generator, with a timeout in seconds."""
    async def _inner():
        async for val in async_gen:
            return val
        return None
    return await asyncio.wait_for(_inner(), timeout=timeout)


# ── PreflightChecker ──────────────────────────────────────────────────────────

class PreflightChecker:
    """
    Runs a sequence of pre-flight checks against a live MAVSDK System and
    returns a PreflightResult.

    Tracks per-session state (whether a geofence has been uploaded) by
    subscribing to bus.mission_uploaded.  Call reset_session() when starting
    a new mission session so stale state is cleared.
    """

    def __init__(self) -> None:
        self._geofence_uploaded: bool = False
        bus.mission_uploaded.connect(self._on_mission_uploaded)

    def _on_mission_uploaded(self) -> None:
        self._geofence_uploaded = True

    def reset_session(self) -> None:
        """Clear session state.  Call before a new flight session begins."""
        self._geofence_uploaded = False

    # ── public entry point ────────────────────────────────────────────────────

    async def run_checks(
        self,
        drone: System,
        mission_offsets: List[OffsetM],
        leg_spacing_m: float,
    ) -> PreflightResult:
        """
        Run all checks in order and return a PreflightResult.

        Intended to run on the MAVSDK asyncio loop (not the Qt main thread).
        Each check catches its own exceptions so a single failure never blocks
        the remaining checks.
        """
        checks: List[PreflightCheck] = []

        # 1 — GPS lock
        checks.append(await self._check_gps(drone))

        # 2 — Home position
        checks.append(await self._check_home(drone))

        # 3 — Waypoint count (pure math, no I/O)
        checks.append(self._check_waypoints(mission_offsets))

        # 4 — Battery feasibility
        batt_check, batt_meta = await self._check_battery(drone, mission_offsets)
        checks.append(batt_check)

        # 5 — Geofence (session state, no I/O)
        checks.append(self._check_geofence())

        return PreflightResult(
            checks=checks,
            estimated_flight_time_min=batt_meta["flight_time_min"],
            estimated_battery_pct=batt_meta["drain_pct"],
            required_battery_pct=batt_meta["required_pct"],
            available_battery_pct=batt_meta["available_pct"],
            is_sim_battery=batt_meta["is_sim"],
        )

    # ── individual checks ─────────────────────────────────────────────────────

    async def _check_gps(self, drone: System) -> PreflightCheck:
        name = "GPS Lock"
        try:
            info = await _first(drone.telemetry.gps_info(), GPS_TIMEOUT_S)
            if info is None:
                return PreflightCheck(name, CheckStatus.FAIL, "No GPS data received")
            fix_int = info.fix_type.value
            fix_label = info.fix_type.name.replace("_", " ")
            if fix_int >= _MIN_FIX_INT:
                return PreflightCheck(
                    name, CheckStatus.PASS,
                    f"{fix_label}  ({info.num_satellites} satellites)",
                )
            return PreflightCheck(
                name, CheckStatus.FAIL,
                f"{fix_label} — 3D fix required before launch",
            )
        except asyncio.TimeoutError:
            return PreflightCheck(
                name, CheckStatus.FAIL,
                f"No GPS data within {GPS_TIMEOUT_S:.0f} s — is the drone connected?",
            )
        except Exception as e:
            return PreflightCheck(name, CheckStatus.FAIL, f"GPS read error: {e}")

    async def _check_home(self, drone: System) -> PreflightCheck:
        name = "Home Position"
        try:
            home = await _first(drone.telemetry.home(), HOME_TIMEOUT_S)
            if home is None:
                return PreflightCheck(name, CheckStatus.FAIL, "Home position not set")
            return PreflightCheck(
                name, CheckStatus.PASS,
                f"Set  ({home.latitude_deg:.5f}°,  {home.longitude_deg:.5f}°,  "
                f"{home.relative_altitude_m:.1f} m AGL)",
            )
        except asyncio.TimeoutError:
            return PreflightCheck(
                name, CheckStatus.FAIL,
                f"Home position not received within {HOME_TIMEOUT_S:.0f} s",
            )
        except Exception as e:
            return PreflightCheck(name, CheckStatus.FAIL, f"Home read error: {e}")

    def _check_waypoints(self, offsets: List[OffsetM]) -> PreflightCheck:
        name = "Waypoint Count"
        n = len(offsets)
        if n < 2:
            return PreflightCheck(
                name, CheckStatus.FAIL,
                f"{n} waypoint(s) — mission requires at least 2",
            )
        return PreflightCheck(name, CheckStatus.PASS, f"{n} waypoints")

    async def _check_battery(
        self,
        drone: System,
        offsets: List[OffsetM],
    ) -> Tuple[PreflightCheck, dict]:
        name = "Battery Feasibility"

        # Compute distance and time regardless of battery availability
        dist_m          = _mission_distance_m(offsets)
        flight_time_min = dist_m / CRUISE_SPEED_M_S / 60.0
        drain_pct       = flight_time_min * BATTERY_DRAIN_PCT_PER_MIN
        required_pct    = drain_pct + RTL_RESERVE_PCT + SAFETY_MARGIN_PCT

        meta = {
            "flight_time_min": round(flight_time_min, 2),
            "drain_pct":       round(drain_pct, 1),
            "required_pct":    round(required_pct, 1),
            "available_pct":   0.0,
            "is_sim":          False,
        }

        try:
            batt = await _first(drone.telemetry.battery(), BATTERY_TIMEOUT_S)
            if batt is None:
                return (
                    PreflightCheck(name, CheckStatus.WARN, "No battery data received"),
                    meta,
                )

            raw = batt.remaining_percent
            if raw < 0:
                # Simulator sentinel value
                meta["is_sim"] = True
                meta["available_pct"] = -1.0
                return (
                    PreflightCheck(
                        name, CheckStatus.WARN,
                        f"Simulation battery — feasibility skipped  "
                        f"(est. {flight_time_min:.1f} min,  "
                        f"~{drain_pct:.0f}% drain + {RTL_RESERVE_PCT:.0f}% RTL "
                        f"+ {SAFETY_MARGIN_PCT:.0f}% margin)",
                    ),
                    meta,
                )

            # Normalise to 0–100 range (MAVSDK may return 0–1 or 0–100)
            available = raw * 100.0 if raw <= 1.0 else float(raw)
            available = round(min(available, 100.0), 1)
            meta["available_pct"] = available

            if available < required_pct:
                return (
                    PreflightCheck(
                        name, CheckStatus.FAIL,
                        f"Insufficient battery: {available:.0f}% available,  "
                        f"{required_pct:.0f}% required  "
                        f"({drain_pct:.0f}% mission + {RTL_RESERVE_PCT:.0f}% RTL "
                        f"+ {SAFETY_MARGIN_PCT:.0f}% margin)",
                    ),
                    meta,
                )

            margin = available - required_pct
            if margin < TIGHT_MARGIN_PCT:
                return (
                    PreflightCheck(
                        name, CheckStatus.WARN,
                        f"Tight margin: {available:.0f}% available,  "
                        f"{required_pct:.0f}% required  "
                        f"(only {margin:.0f}% buffer — consider recharging)",
                    ),
                    meta,
                )

            return (
                PreflightCheck(
                    name, CheckStatus.PASS,
                    f"{available:.0f}% available,  {required_pct:.0f}% required  "
                    f"({margin:.0f}% margin)",
                ),
                meta,
            )

        except asyncio.TimeoutError:
            return (
                PreflightCheck(
                    name, CheckStatus.WARN,
                    f"Battery data not received within {BATTERY_TIMEOUT_S:.0f} s",
                ),
                meta,
            )
        except Exception as e:
            return (
                PreflightCheck(name, CheckStatus.WARN, f"Battery read error: {e}"),
                meta,
            )

    def _check_geofence(self) -> PreflightCheck:
        name = "Geofence"
        if not self._geofence_uploaded:
            return PreflightCheck(
                name, CheckStatus.WARN,
                "No geofence uploaded this session — "
                "upload a mission from the Mission Planner tab first",
            )
        return PreflightCheck(name, CheckStatus.PASS, "Geofence active this session")
