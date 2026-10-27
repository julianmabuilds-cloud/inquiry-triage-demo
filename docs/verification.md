# Verification

Validated on 2026-10-27 using a clean copy of the Git-eligible source files and a newly created virtual environment. The original development environment was not reused.

Environment: macOS arm64, Python 3.9.6, pip 25.1.1, dependencies from `requirements.lock`.

| Check | Result |
| --- | --- |
| Install locked dependencies from PyPI | Passed |
| `python -m pip install --no-build-isolation --no-deps -e .` | Passed; installation metadata confirms editable mode |
| Offline CLI with `data/inquiries.json` | 8 total, 2 qualified, 5 review, 1 duplicate, 10 provider attempts |
| `pytest -q` | **92 passed** |
| `mypy src` | Passed: **10 source files** |
| `ruff check src tests scripts` | Passed |
| `ruff format --check src tests scripts` | Passed: 15 files |
| `python -m pip check` | No broken requirements |
| `python scripts/http_smoke.py` | Real loopback HTTP: health 200, triage 200 matching fixtures, empty batch 422; server shut down |

The OpenAI and Ollama adapters are tested using synthetic `httpx.MockTransport` responses. The suite checks schema/request construction, parsing, retries, cancellation, missing fields, error routing, provider selection, and duplicate/export behavior. Real asynchronous HTTP transport is blocked in automated tests.

The HTTP smoke test uses the real FastAPI app but the fixture provider. No local model inference, model download, paid API call, extraction accuracy benchmark, or production validation was performed. Other operating systems and Python versions have not been verified.

Repository preparation excluded virtual environments, IDE configuration, generated exports, local model inventory and raw machine-specific logs. The blank `.env.example`, synthetic fixtures and expected outputs are intentional source artifacts. No license has been selected as part of this preparation.
