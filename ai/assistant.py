import json
import re
from typing import Optional

from ai.backends.base import AIBackend

_MISSION_SYSTEM = """\
You are a mission planning assistant for an autonomous drone ground control station.
You output ONLY valid JSON — no prose, no markdown fences, no explanation.
The JSON must match exactly: {"polygon": [[lat, lon], ...], "leg_spacing_m": <number>}

━━━ COORDINATE MATH (mandatory) ━━━
Convert metre dimensions to degrees using these exact formulas:
  lat_deg_per_m = 1 / 111000
  lon_deg_per_m = 1 / (111000 * cos(lat_radians))

For a rectangle W metres wide (east-west) and H metres tall (north-south)
centred on (lat, lon):
  half_lat = (H / 2) / 111000
  half_lon = (W / 2) / (111000 * cos(lat * pi / 180))
  NW = [lat + half_lat,  lon - half_lon]
  NE = [lat + half_lat,  lon + half_lon]
  SE = [lat - half_lat,  lon + half_lon]
  SW = [lat - half_lat,  lon - half_lon]

WORKED EXAMPLE — 100 m square centred on lat=32.923, lon=-117.259:
  cos(32.923° in radians) = cos(0.57455) ≈ 0.83867
  half_lat = 50 / 111000               ≈ 0.000450
  half_lon = 50 / (111000 × 0.83867)  ≈ 0.000537
  NW = [32.923450, -117.259537]
  NE = [32.923450, -117.258463]
  SE = [32.922550, -117.258463]
  SW = [32.922550, -117.259537]
  → polygon: [[32.923450,-117.259537],[32.923450,-117.258463],
              [32.922550,-117.258463],[32.922550,-117.259537]]

━━━ POLYGON RULES ━━━
- Always produce exactly 4 vertices for square or rectangular areas.
- List vertices clockwise from NW: [NW, NE, SE, SW]. Do NOT repeat the first vertex.
- For irregular or explicitly non-rectangular areas use as many vertices as needed.
- If no location is specified, use the drone position as the centre.

━━━ LEG SPACING RULE ━━━
leg_spacing_m must never exceed half the shortest polygon dimension.
Example: for a 50 m × 80 m rectangle, shortest side = 50 m → leg_spacing_m ≤ 25.
"""

_REPORT_SYSTEM = """\
You are a post-flight reporting assistant for an autonomous drone.
Write a concise 3–5 sentence plain-text mission summary suitable for an operator log.
Cover: mission outcome, key telemetry highlights, and any notable events.
Do not use bullet points, headers, or markdown — plain prose only.
"""


class AIAssistant:
    """High-level drone assistant that delegates inference to a pluggable backend."""

    def __init__(self, backend: AIBackend) -> None:
        self._backend = backend

    # ── public methods ────────────────────────────────────────────────────────

    async def assist_mission(
        self,
        description: str,
        drone_position: Optional[tuple[float, float]] = None,
    ) -> dict:
        """
        Ask the AI to produce a survey polygon + leg spacing from a natural-language
        description.  Returns a dict with keys 'polygon' and 'leg_spacing_m'.
        Raises ValueError if the response cannot be parsed.
        """
        location_hint = ""
        if drone_position:
            lat, lon = drone_position
            location_hint = f"\nCurrent drone position: ({lat:.6f}, {lon:.6f}). Use this as the approximate centre if no other location is specified."

        prompt = (
            f"Plan a drone survey mission based on this description:\n{description}"
            f"{location_hint}\n\n"
            "Respond with JSON only."
        )

        raw = await self._backend.generate(prompt, system=_MISSION_SYSTEM)
        return self._parse_json(raw)

    async def generate_mission_report(self, telemetry_data: dict) -> str:
        """
        Produce a plain-text post-flight summary from a telemetry snapshot dict.
        """
        lines = "\n".join(f"{k}: {v}" for k, v in telemetry_data.items())
        prompt = f"Generate a post-flight report from the following telemetry data:\n{lines}"
        return await self._backend.generate(prompt, system=_REPORT_SYSTEM)

    # ── internal helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _parse_json(text: str) -> dict:
        # Strip optional markdown code fences (```json ... ```)
        text = re.sub(r"```[a-z]*\n?", "", text).strip()
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"AI response was not valid JSON: {exc}\nRaw: {text!r}") from exc
        if "polygon" not in data or "leg_spacing_m" not in data:
            raise ValueError(f"AI JSON missing required keys. Got: {list(data.keys())}")
        return data
