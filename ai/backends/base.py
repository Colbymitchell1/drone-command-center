from abc import ABC, abstractmethod


class AIBackend(ABC):
    """Abstract base class for pluggable AI backends."""

    @abstractmethod
    async def generate(self, prompt: str, system: str = "") -> str:
        """Send a prompt and return the model's response text."""

    @abstractmethod
    async def is_available(self) -> bool:
        """Return True if the backend is reachable and ready."""
