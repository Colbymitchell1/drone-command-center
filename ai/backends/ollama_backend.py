import asyncio
import json
import os
import urllib.error
import urllib.request
from typing import Any

from ai.backends.base import AIBackend

_BASE_URL = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
_MODEL = "llama3.1"
_GENERATE_TIMEOUT = 300  # seconds — CPU inference can be slow; streaming keeps socket alive
_PROBE_TIMEOUT = 2       # seconds — availability check


def _post_streaming(url: str, payload: dict[str, Any], timeout: int) -> str:
    """
    POST to the Ollama streaming endpoint and accumulate token chunks.

    The /api/generate streaming response is a sequence of newline-delimited
    JSON objects, each with a "response" field containing the next token(s).
    The final object has "done": true.  We accumulate all "response" values
    and return the complete text.

    The socket timeout applies to the time between *individual* reads, not to
    the total inference time, so a 300 s timeout here means "300 s of silence
    before we give up" — the stream itself can run arbitrarily longer.
    """
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    parts: list[str] = []
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        for raw_line in resp:
            line = raw_line.strip()
            if not line:
                continue
            chunk = json.loads(line)
            parts.append(chunk.get("response", ""))
            if chunk.get("done"):
                break
    return "".join(parts)


def _get(url: str, timeout: int) -> None:
    with urllib.request.urlopen(url, timeout=timeout):
        pass


class OllamaBackend(AIBackend):
    """Calls the local Ollama REST API (http://localhost:11434)."""

    async def generate(self, prompt: str, system: str = "") -> str:
        payload: dict[str, Any] = {
            "model": _MODEL,
            "prompt": prompt,
            "stream": True,
        }
        if system:
            payload["system"] = system

        return await asyncio.to_thread(
            _post_streaming, f"{_BASE_URL}/api/generate", payload, _GENERATE_TIMEOUT
        )

    async def is_available(self) -> bool:
        try:
            await asyncio.to_thread(_get, f"{_BASE_URL}/api/tags", _PROBE_TIMEOUT)
            return True
        except Exception:
            return False
