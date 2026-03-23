import asyncio
import logging
import os
from typing import Optional

from ai.assistant import AIAssistant
from ai.backends.base import AIBackend
from ai.backends.claude_backend import ClaudeBackend
from ai.backends.ollama_backend import OllamaBackend

logger = logging.getLogger(__name__)

_BACKEND_ENV = "AI_BACKEND"   # "ollama" (default) | "claude"


class AIService:
    """
    Selects and initialises an AI backend at startup.

    Resolution order:
      1. AI_BACKEND env var ("ollama" or "claude").
      2. If the selected backend is unavailable, fall back to the other.
      3. If neither is available, self.available == False and assistant is None.

    All availability probing is async — call await ai_service.initialise(loop)
    once on startup before accessing .assistant.
    """

    def __init__(self) -> None:
        self._backend: Optional[AIBackend] = None
        self._assistant: Optional[AIAssistant] = None

    # ── public API ────────────────────────────────────────────────────────────

    @property
    def available(self) -> bool:
        return self._backend is not None

    @property
    def assistant(self) -> Optional[AIAssistant]:
        return self._assistant

    @property
    def backend_name(self) -> str:
        if isinstance(self._backend, OllamaBackend):
            return "Ollama (local)"
        if isinstance(self._backend, ClaudeBackend):
            return "Claude (Anthropic)"
        return "None"

    async def initialise(self) -> None:
        """Probe backends and select the best available one."""
        preferred = os.environ.get(_BACKEND_ENV, "ollama").lower()

        primary, fallback = self._build_pair(preferred)

        if await primary.is_available():
            self._backend = primary
            logger.info("AI backend selected: %s", self.backend_name)
            self._assistant = AIAssistant(self._backend)
            return

        logger.warning("Primary AI backend unavailable, trying fallback…")

        if await fallback.is_available():
            self._backend = fallback
            logger.info("AI fallback backend selected: %s", self.backend_name)
            self._assistant = AIAssistant(self._backend)
            return

        logger.warning("No AI backend available — AI features disabled.")

    # ── internal ──────────────────────────────────────────────────────────────

    @staticmethod
    def _build_pair(preferred: str) -> tuple[AIBackend, AIBackend]:
        ollama = OllamaBackend()
        claude = ClaudeBackend()
        if preferred == "claude":
            return claude, ollama
        return ollama, claude
