import os
from typing import Any, Optional

from fastapi import FastAPI, HTTPException

from .config import processing_options, provider_mode, select_provider
from .core import process, summary
from .models import Batch
from .provider import Provider


def create_app(provider: Optional[Provider] = None) -> FastAPI:
    app = FastAPI(title="Inquiry Triage Demo", version="0.2.0")
    backend = provider if provider is not None else select_provider()
    mode = provider_mode(backend)

    @app.get("/health")
    async def health() -> dict[str, str]:
        # Configuration readiness, not a remote-model connectivity or quality check.
        return {"status": "ok", "mode": mode}

    @app.post("/triage")
    async def triage(batch: Batch) -> dict[str, Any]:
        try:
            results = await process(batch.inquiries, backend, **processing_options(backend))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"mode": mode, "summary": summary(results), "results": results}

    return app


def create_live_app() -> FastAPI:
    # Separate entry point: the default factory cannot enable live mode via environment.
    provider = select_provider("openai", allow_live=os.environ.get("TRIAGE_ALLOW_LIVE") == "1")
    return create_app(provider)


def create_local_app() -> FastAPI:
    """Explicit factory; default create_app stays offline regardless of environment."""
    return create_app(select_provider("ollama"))
