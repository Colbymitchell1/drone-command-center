import os

import anthropic

from ai.backends.base import AIBackend

_MODEL = "claude-sonnet-4-6"
_MAX_TOKENS = 1024


class ClaudeBackend(AIBackend):
    """Calls the Anthropic API using the ANTHROPIC_API_KEY environment variable."""

    def __init__(self) -> None:
        self._client = anthropic.AsyncAnthropic()  # reads ANTHROPIC_API_KEY automatically

    async def generate(self, prompt: str, system: str = "") -> str:
        kwargs = {
            "model": _MODEL,
            "max_tokens": _MAX_TOKENS,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system

        msg = await self._client.messages.create(**kwargs)
        return msg.content[0].text

    async def is_available(self) -> bool:
        return bool(os.environ.get("ANTHROPIC_API_KEY"))
