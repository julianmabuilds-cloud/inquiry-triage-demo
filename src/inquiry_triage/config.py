"""Configuration is explicit; never load .env or generic OPENAI_API_KEY variables."""

import os
from pathlib import Path
from typing import Mapping, Optional, TypedDict

from .openai_provider import OpenAIProvider
from .ollama_provider import OllamaProvider
from .provider import FixtureProvider, Provider


def select_provider(
    name: str = "fixture",
    *,
    allow_live: bool = False,
    fixtures: Path = Path("data/fixtures.json"),
    env: Optional[Mapping[str, str]] = None,
) -> Provider:
    if name == "fixture":
        return FixtureProvider(fixtures)  # No environment access in offline mode.
    if name == "ollama":
        values = os.environ if env is None else env
        return OllamaProvider(
            base_url=values.get("TRIAGE_OLLAMA_URL", ""),
            model=values.get("TRIAGE_OLLAMA_MODEL", ""),
        )
    if name != "openai":
        raise ValueError("Provider must be fixture, openai or ollama")
    if not allow_live:
        raise ValueError("OpenAI requires explicit live opt-in")
    values = os.environ if env is None else env
    return OpenAIProvider(
        api_key=values.get("TRIAGE_OPENAI_API_KEY", ""),
        model=values.get("TRIAGE_OPENAI_MODEL", ""),
        allow_live=True,
    )


def provider_mode(provider: Provider) -> str:
    if isinstance(provider, FixtureProvider):
        return "offline_fixture"
    if isinstance(provider, (OpenAIProvider, OllamaProvider)):
        return provider.mode
    return "custom_provider"


class ProcessingOptions(TypedDict):
    timeout: float
    retry_delay: float


def processing_options(provider: Provider) -> ProcessingOptions:
    if isinstance(provider, OllamaProvider):
        return {"timeout": 60.0, "retry_delay": 0.5}
    if isinstance(provider, OpenAIProvider):
        return {"timeout": 30.0, "retry_delay": 0.5}
    return {"timeout": 0.02, "retry_delay": 0.001}
