"""
app/services/llm_review.py

LLM-powered mission review service.

Sends MissionPlan and FlightLog objects to an AI backend and returns
structured findings. Uses a priority fallback chain:

    1. Local Ollama (Crucible over Tailscale, or localhost)
    2. Claude API (if ANTHROPIC_API_KEY is set)
    3. Graceful degradation (returns a skipped review, does not crash)

The LLM is NEVER in the control path. All output is advisory.
Operators must explicitly acknowledge findings before arming.

Usage:
    reviewer = LLMReviewService()
    review = await reviewer.pre_mission_review(plan)

    if review.is_blocked:
        # show blockers -- do not allow arming

    post = await reviewer.post_mission_review(plan, flight_log)
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Optional

from app.models.domain import (
    FlightLog,
    LLMFinding,
    MissionPlan,
    MissionRiskReview,
    PostMissionReview,
    RiskLevel,
)
from ai.backends.base import AIBackend
from ai.backends.ollama_backend import OllamaBackend
from ai.backends.claude_backend import ClaudeBackend

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_PRE_MISSION_SYSTEM = """\
You are a local mission review assistant for a civilian Part 107 drone ground station.

Your job is to review the provided mission JSON and identify possible operational
risks, missing information, or inconsistencies.

Rules:
- You do NOT approve flights.
- You do NOT replace the remote pilot in command.
- You do NOT provide legal determinations.
- You ONLY flag items for human review.
- Respond with valid JSON only -- no prose, no markdown fences, no explanation.

Your response must match this exact structure:
{
  "risk_level": "low" | "medium" | "high" | "blocked",
  "blocking_items": [],
  "warnings": [{"category": "string", "message": "string"}],
  "missing_information": [],
  "regulatory_flags": [],
  "vehicle_readiness_flags": [],
  "airspace_weather_flags": [],
  "suggested_operator_questions": [],
  "plain_english_summary": "string"
}

risk_level rules:
- "blocked": hard stop, mission should not proceed
- "high": serious concerns requiring operator attention
- "medium": advisories the operator should review
- "low": no significant concerns found
"""

_PRE_MISSION_PROMPT = """\
Review this drone mission plan and return your findings as JSON only.

Mission JSON:
{mission_json}
"""

_POST_MISSION_SYSTEM = """\
You are a post-mission review assistant for a civilian Part 107 drone operation.

Review the provided mission plan and flight log. Identify anomalies, deviations
from plan, and items requiring operator follow-up.

Rules:
- Plain factual analysis only.
- Do NOT speculate about causes without evidence in the data.
- Respond with valid JSON only -- no prose, no markdown fences, no explanation.

Your response must match this exact structure:
{
  "findings": [{"category": "string", "message": "string"}],
  "recommended_followups": [],
  "plain_english_summary": "string"
}
"""

_POST_MISSION_PROMPT = """\
Review this completed drone mission and return your findings as JSON only.

Mission Plan:
{mission_json}

Flight Log:
{log_json}
"""


# ---------------------------------------------------------------------------
# Backend fallback chain
# ---------------------------------------------------------------------------


class BackendChain:
    """
    Tries backends in priority order until one is available.
    Returns (backend, name) or (None, None) if all fail.
    """

    @staticmethod
    async def resolve() -> tuple[Optional[AIBackend], Optional[str]]:
        candidates = BackendChain._build_candidates()

        for name, backend in candidates:
            try:
                if await backend.is_available():
                    log.info(f"LLM backend selected: {name}")
                    return backend, name
            except Exception as e:
                log.debug(f"Backend {name} unavailable: {e}")

        log.warning("No LLM backend available -- review will be skipped")
        return None, None

    @staticmethod
    def _build_candidates() -> list[tuple[str, AIBackend]]:
        candidates = []

        # 1. Crucible over Tailscale (primary)
        crucible_url = os.environ.get("OLLAMA_HOST", "http://100.117.188.114:11434")
        crucible = OllamaBackend()
        candidates.append((f"Ollama ({crucible_url})", crucible))

        # 2. Claude API (fallback if key is set)
        if os.environ.get("ANTHROPIC_API_KEY"):
            try:
                candidates.append(("Claude API", ClaudeBackend()))
            except Exception as e:
                log.debug(f"Claude backend init failed: {e}")

        return candidates


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def _mission_to_json(plan: MissionPlan) -> str:
    """Serialize MissionPlan to a compact JSON string for the prompt."""
    waypoints = [
        {"lat": wp.lat, "lon": wp.lon, "alt_m": wp.alt_m}
        for wp in plan.geometry.waypoints
    ]
    d = {
        "mission_id": plan.mission_id,
        "mission_type": plan.mission_type.value,
        "site_name": plan.site_name,
        "notes": plan.notes,
        "waypoint_count": len(waypoints),
        "waypoints": waypoints,
        "geofence_polygon_points": len(plan.geometry.geofence_polygon),
        "regulatory": {
            "operating_rule": plan.regulatory.operating_rule.value,
            "night_operation": plan.regulatory.night_operation,
            "over_people": plan.regulatory.over_people,
            "over_moving_vehicles": plan.regulatory.over_moving_vehicles,
            "bvlos": plan.regulatory.bvlos,
            "max_altitude_agl_ft": plan.regulatory.max_altitude_agl_ft,
            "requires_controlled_airspace": plan.regulatory.requires_controlled_airspace,
        },
        "failsafes": {
            "lost_link_action": plan.failsafes.lost_link_action,
            "low_battery_action": plan.failsafes.low_battery_action,
            "geofence_breach_action": plan.failsafes.geofence_breach_action,
        },
    }
    return json.dumps(d, indent=2)


def _log_to_json(flight_log: FlightLog) -> str:
    """Serialize FlightLog summary to JSON for the prompt. Excludes raw telemetry track."""
    d = {
        "mission_id": flight_log.mission_id,
        "started_at_utc": flight_log.started_at_utc.isoformat() if flight_log.started_at_utc else None,
        "ended_at_utc": flight_log.ended_at_utc.isoformat() if flight_log.ended_at_utc else None,
        "vehicle_id": flight_log.vehicle_id,
        "adapter_type": flight_log.adapter_type,
        "preflight": {
            "battery_percent": flight_log.preflight.battery_percent,
            "gps_fix": flight_log.preflight.gps_fix,
            "home_position_set": flight_log.preflight.home_position_set,
            "airspace_checked": flight_log.preflight.airspace_checked,
            "remote_id_checked": flight_log.preflight.remote_id_checked,
        },
        "execution": {
            "max_altitude_agl_ft": flight_log.execution.max_altitude_agl_ft,
            "max_distance_from_home_m": flight_log.execution.max_distance_from_home_m,
            "flight_time_seconds": flight_log.execution.flight_time_seconds,
            "mission_completed": flight_log.execution.mission_completed,
            "abort_reason": flight_log.execution.abort_reason,
        },
        "incidents": {
            "accident_report_required": flight_log.incidents.accident_report_required,
            "injury_or_loss_of_consciousness": flight_log.incidents.injury_or_loss_of_consciousness,
            "property_damage_over_500": flight_log.incidents.property_damage_over_500,
            "notes": flight_log.incidents.notes,
        },
        "event_count": len(flight_log.events),
        "events": flight_log.events,
    }
    return json.dumps(d, indent=2)


# ---------------------------------------------------------------------------
# JSON parsing helpers
# ---------------------------------------------------------------------------


def _strip_fences(text: str) -> str:
    """Remove markdown code fences if the model adds them despite instructions."""
    return re.sub(r"```[a-z]*\n?", "", text).strip()


def _parse_pre_mission(raw: str, model_name: str) -> MissionRiskReview:
    """Parse LLM JSON output into a MissionRiskReview. Defensive -- never raises."""
    try:
        data = json.loads(_strip_fences(raw))
    except json.JSONDecodeError as e:
        log.warning(f"Pre-mission JSON parse failed: {e}\nRaw: {raw[:300]}")
        return _skipped_review(model_name, reason="LLM returned invalid JSON")

    return MissionRiskReview(
        risk_level=_safe_risk_level(data.get("risk_level", "medium")),
        llm_model=model_name,
        blocking_items=data.get("blocking_items", []),
        warnings=[
            LLMFinding(
                category=w.get("category", "general"),
                message=w.get("message", ""),
            )
            for w in data.get("warnings", [])
        ],
        missing_information=data.get("missing_information", []),
        regulatory_flags=data.get("regulatory_flags", []),
        vehicle_readiness_flags=data.get("vehicle_readiness_flags", []),
        airspace_weather_flags=data.get("airspace_weather_flags", []),
        suggested_operator_questions=data.get("suggested_operator_questions", []),
        plain_english_summary=data.get("plain_english_summary", ""),
        operator_acknowledged=False,
        reviewed_at_utc=datetime.now(timezone.utc),
    )


def _parse_post_mission(raw: str, model_name: str) -> PostMissionReview:
    """Parse LLM JSON output into a PostMissionReview. Defensive -- never raises."""
    try:
        data = json.loads(_strip_fences(raw))
    except json.JSONDecodeError as e:
        log.warning(f"Post-mission JSON parse failed: {e}\nRaw: {raw[:300]}")
        return PostMissionReview(
            llm_model=model_name,
            plain_english_summary="Review could not be parsed from LLM output.",
            reviewed_at_utc=datetime.now(timezone.utc),
        )

    return PostMissionReview(
        llm_model=model_name,
        findings=[
            LLMFinding(
                category=f.get("category", "general"),
                message=f.get("message", ""),
            )
            for f in data.get("findings", [])
        ],
        recommended_followups=data.get("recommended_followups", []),
        plain_english_summary=data.get("plain_english_summary", ""),
        reviewed_at_utc=datetime.now(timezone.utc),
    )


def _safe_risk_level(value: str) -> RiskLevel:
    try:
        return RiskLevel(value.lower())
    except ValueError:
        return RiskLevel.MEDIUM


def _skipped_review(model_name: str = "", reason: str = "No LLM backend available") -> MissionRiskReview:
    return MissionRiskReview(
        risk_level=RiskLevel.LOW,
        llm_model=model_name or "none",
        missing_information=[reason],
        plain_english_summary=f"Automated review skipped: {reason}. Operator should manually verify all checklist items.",
        reviewed_at_utc=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# LLMReviewService
# ---------------------------------------------------------------------------


class LLMReviewService:
    """
    Orchestrates pre- and post-mission LLM review using the best
    available backend in the fallback chain.

    All methods are safe to call even if no backend is reachable --
    they return a degraded review rather than raising.
    """

    def __init__(self):
        # Backend resolved lazily on first call so startup isn't blocked
        self._backend: Optional[AIBackend] = None
        self._backend_name: Optional[str] = None
        self._resolved = False

    async def _get_backend(self) -> tuple[Optional[AIBackend], str]:
        if not self._resolved:
            self._backend, self._backend_name = await BackendChain.resolve()
            self._resolved = True
        return self._backend, self._backend_name or "none"

    async def pre_mission_review(self, plan: MissionPlan) -> MissionRiskReview:
        """
        Send a MissionPlan to the LLM for risk review.
        Returns a MissionRiskReview -- never raises.

        The returned review has operator_acknowledged=False.
        The UI must require explicit operator acknowledgment before arming.
        """
        backend, name = await self._get_backend()

        if backend is None:
            return _skipped_review(reason="No LLM backend reachable")

        mission_json = _mission_to_json(plan)
        prompt = _PRE_MISSION_PROMPT.format(mission_json=mission_json)

        try:
            log.info(f"Running pre-mission review via {name}...")
            raw = await backend.generate(prompt, system=_PRE_MISSION_SYSTEM)
            review = _parse_pre_mission(raw, name)
            log.info(f"Pre-mission review complete: risk_level={review.risk_level.value}")
            return review
        except Exception as e:
            log.error(f"Pre-mission review failed: {e}")
            return _skipped_review(name, reason=f"Review error: {e}")

    async def post_mission_review(
        self,
        plan: MissionPlan,
        flight_log: FlightLog,
    ) -> PostMissionReview:
        """
        Send a MissionPlan + FlightLog to the LLM for post-mission analysis.
        Returns a PostMissionReview -- never raises.
        """
        backend, name = await self._get_backend()

        if backend is None:
            return PostMissionReview(
                llm_model="none",
                plain_english_summary="Post-mission review skipped: no LLM backend reachable.",
                reviewed_at_utc=datetime.now(timezone.utc),
            )

        mission_json = _mission_to_json(plan)
        log_json = _log_to_json(flight_log)
        prompt = _POST_MISSION_PROMPT.format(
            mission_json=mission_json,
            log_json=log_json,
        )

        try:
            log.info(f"Running post-mission review via {name}...")
            raw = await backend.generate(prompt, system=_POST_MISSION_SYSTEM)
            review = _parse_post_mission(raw, name)
            log.info("Post-mission review complete")
            return review
        except Exception as e:
            log.error(f"Post-mission review failed: {e}")
            return PostMissionReview(
                llm_model=name,
                plain_english_summary=f"Post-mission review error: {e}",
                reviewed_at_utc=datetime.now(timezone.utc),
            )

    def reset_backend(self) -> None:
        """Force re-resolution of the backend chain on next call. Useful if Crucible comes online."""
        self._backend = None
        self._backend_name = None
        self._resolved = False
