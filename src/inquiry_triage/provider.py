"""Fixture lookup, NOT a language model. No credentials or network access."""

import asyncio
import json
from pathlib import Path
from typing import Any, Dict, Protocol


def normalize(text: str) -> str:
    return " ".join(text.casefold().split())


class UnsupportedInput(Exception):
    pass


class ProviderError(Exception):
    """Only constant/sanitized codes cross the adapter boundary, never raw responses."""

    def __init__(self, code: str, *, retryable: bool) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


class Provider(Protocol):
    async def extract(self, text: str, attempt: int) -> Dict[str, Any]:
        """Return schema-shaped data, preserving absent facts as null/unknown.

        Implementations must support async cancellation and set transport timeouts.
        attempt is scoped to this record, avoiding shared mutable retry counters.
        """
        ...


class FixtureProvider:
    def __init__(self, path: Path):
        self.fixtures = {normalize(item["text"]): item for item in json.loads(path.read_text())}

    async def extract(self, text: str, attempt: int) -> Dict[str, Any]:
        item = self.fixtures.get(normalize(text))
        if item is None:
            raise UnsupportedInput("No exact synthetic fixture")
        behavior = item.get("behavior", "success")
        if behavior == "timeout" or (behavior == "transient_timeout" and attempt == 1):
            await asyncio.sleep(60)  # Cancelled by the core's short per-attempt deadline.
        if behavior == "invalid":
            return {"budget_usd": "invented"}
        return item["fields"]
