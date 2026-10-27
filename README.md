# Inquiry Triage

Turn an English sales inquiry into validated fields, a reproducible business score, and an explicit human-review decision. This small Python portfolio demo shows how to add a model-provider boundary to an existing backend while keeping business rules outside the model.

**Independent demo, synthetic data, not a client delivery or production-ready system.** The default runs entirely offline using hand-authored fixtures. OpenAI and local Ollama adapters are implemented and tested with mock HTTP responses; real model extraction quality has not been evaluated. No emails are sent and no real CRM is connected.

## What it demonstrates

- One processing core shared by a batch CLI and FastAPI.
- Strict nullable extraction fields: missing facts remain `null` or `unknown` and trigger review.
- Deterministic scoring, batch-local duplicate detection, bounded retries, timeouts, and explicit error reasons.
- JSON/CSV exports and a clearly labelled **local mock CRM** file.
- Provider modes recorded separately from success: fixture, mock HTTP, or actual selected adapter.

```text
CLI JSON / FastAPI request
          ↓
normalize + batch-local duplicate check
          ↓
provider → deadline/retry → schema validation
          ↓
deterministic score → qualified / review / duplicate
          ↓
API response or CLI JSON / CSV / local mock CRM
```

## 1. Install from a fresh clone

Prerequisites: Git, Python **3.9 or later**, and network access to PyPI for initial setup. The verified environment is **Python 3.9.6 on macOS arm64**; other Python versions and operating systems have not been validated. No model, API account, Node.js, or database is needed for the offline demo.

Clone this repository, then open its root directory.

```bash
git clone https://github.com/julianmabuilds-cloud/inquiry-triage-demo.git
cd inquiry-triage-demo
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.lock
python -m pip install --no-build-isolation --no-deps -e .
```

`requirements.lock` pins the development tools and dependencies, including pip 25.1.1 for editable-install support. It is a version pin list, not a hash-verified cross-platform lock. The final command installs this project's CLI in editable mode: source edits are used without reinstalling. Metadata changes (such as adding dependencies or commands) still require reinstalling. All packages go into `.venv`; no system Python changes are needed.

If this checkout is already installed, just `cd` into it and run `source .venv/bin/activate`. Do not recreate the environment each time. Older local installs may be non-editable; rerun the two pip commands above to switch them to editable mode. Windows activation differs (`.venv\Scripts\Activate.ps1` in PowerShell); the commands below were verified on macOS.

> 中文提示：先运行 fixture 模式，无需下载模型或填写 API key。终端需停留在项目根目录。

## 2. Run the offline CLI

```bash
inquiry-triage --provider fixture --input data/inquiries.json --out out
```

Expected final stdout (event logs also appear on stderr):

```json
{"duplicate": 1, "mode": "offline_fixture", "provider_attempts": 10, "qualified": 2, "review": 5, "total": 8}
```

```bash
cat out/run.json
cat out/results.json
cat out/review.json
```

| File | Contents |
| --- | --- |
| `out/run.json` | Provider mode and aggregate counts; keep with exported results |
| `out/results.json` | Fields, decisions, score reasons, attempts and duplicate references |
| `out/results.csv` | Spreadsheet-friendly export with formula-prefix escaping |
| `out/review.json` | Five synthetic records requiring human review |
| `out/mock_crm.json` | `mock_only: true`; two qualified records, no external writes |

Reruns **overwrite** these files in the selected output directory; they do not append CRM records. Writes are not transactional and can be partial on a filesystem error. `out/` and `out-*/` are ignored by Git. Checked-in expected artifacts live in `data/expected/`.

The CLI comes from `[project.scripts]` in `pyproject.toml`, which maps `inquiry-triage` to `inquiry_triage.cli:main`. Equivalent invocation:

```bash
python -m inquiry_triage.cli --provider fixture --input data/inquiries.json --out out
```

## 3. Run the localhost API

In the activated project terminal:

```bash
uvicorn inquiry_triage.api:create_app --factory --host 127.0.0.1 --port 8000
```

Keep it running. In a second terminal:

```bash
curl -sS http://127.0.0.1:8000/health
curl -sS http://127.0.0.1:8000/triage \
  -H 'Content-Type: application/json' \
  -d '{"inquiries":[{"id":"demo-1","text":"We need a backend integration. Please share details."}]}'
```

Health returns `{"status":"ok","mode":"offline_fixture"}`. The inquiry returns `review`, score **40**, and `null` for company, contact email, budget, and timeline. The sentence matches an existing fixture; arbitrary text in fixture mode returns `unsupported_fixture_input`, not invented extraction.

Open **http://127.0.0.1:8000/docs** for interactive API documentation. To submit all eight examples, run from the project root:

```bash
curl -sS http://127.0.0.1:8000/triage \
  -H 'Content-Type: application/json' --data-binary @data/inquiries.json
```

The API accepts 1–100 inquiries, unique IDs, and text up to 10,000 characters per record. Invalid input returns HTTP 422. Valid batches return HTTP 200 with per-record decisions, including provider errors. API requests return data; they do not write CLI output files. `/health` reports local configuration, not model connectivity or quality.

**Stop the server with Ctrl+C in its terminal.** Run `deactivate` to leave the virtual environment. Normal CLI runs exit when finished; Ctrl+C interrupts a run.

## 4. Run the checks

With the environment activated and the project root as the working directory:

```bash
pytest -q
mypy src
ruff check src tests scripts
ruff format --check src tests scripts
python -m pip check
python scripts/http_smoke.py
```

The last command starts a temporary server on `127.0.0.1:8765`, checks real HTTP responses with **fixtures**, and shuts it down. The automated suite covers both adapters through `httpx.MockTransport`; real asynchronous HTTP transport is blocked in tests. It does not call a paid API or local model. See [verification](docs/verification.md).

## 5. Optional: an already-installed local model through Ollama

Prerequisites: an installed Ollama runtime, an already-downloaded local text-capable model, and a running loopback server. **This project does not install Ollama, download models, or change daemon settings.** Ollama is the runtime; Llama and Qwen are different model families.

Inspect your runtime and exact installed model tags first:

```bash
ollama list
```

Set the actual loopback IP/port and exact installed tag in your shell. For example, **only if your machine already has `qwen3-vl:4b` and Ollama listens on this address**:

```bash
export TRIAGE_OLLAMA_URL=http://127.0.0.1:11434
export TRIAGE_OLLAMA_MODEL=qwen3-vl:4b
inquiry-triage --provider ollama --input data/local-smoke.json --out out-local
```

For an installed Llama, replace the model variable with its exact tag from `ollama list`. Qwen is not Llama. These commands perform actual local inference; outputs may differ from fixtures. Compare the two records in `out-local/results.json` to the manually authored fields in `data/local-smoke-expected.json`.

For an API using the same local provider, stop the fixture server first, then:

```bash
uvicorn inquiry_triage.api:create_local_app --factory --host 127.0.0.1 --port 8000
```

Use the same `/triage` endpoint, preferably `--data-binary @data/local-smoke.json` for the small sample. The default `create_app` factory always remains fixture-based.

The adapter implements Ollama **`POST /api/chat`**, with JSON Schema in `format` and `stream: false`. It reads `/api/show` metadata before sending inquiry text and rejects remote aliases. Only explicit HTTP loopback IPs and ports are accepted; no redirects, environment proxies, cloud model names, or arbitrary remote hosts. This assumes a trusted local daemon.

Settings in code: temperature 0, seed 42, context 4096 tokens, output limit 512 tokens, `keep_alive: "0"` to release the model after each response. Thinking is disabled only if metadata advertises support. Outer deadline: 60 seconds per attempt; HTTP connect timeout: 3 seconds; other HTTP phase timeouts: 55 seconds; at most two attempts by default. There is no CLI timeout flag. Cold loads may be slow; deterministic settings do not establish exact reproducibility or extraction quality.

Protocol references: [chat API](https://docs.ollama.com/api/chat), [structured outputs](https://docs.ollama.com/capabilities/structured-outputs), [official API types](https://github.com/ollama/ollama/blob/main/api/types.go), reviewed 2026-10-27. Local inference has not been evaluated for this demo.

## 6. Optional: OpenAI (paid requests, explicit opt-in)

Only use an API account and model you are authorized to use. This mode sends inquiry text to OpenAI and may incur charges. Set `TRIAGE_OPENAI_API_KEY` securely in your own shell and set `TRIAGE_OPENAI_MODEL` to an available model/snapshot supporting Responses Structured Outputs. No credential or model is defaulted. `.env.example` is a blank reference; **`.env` is never automatically loaded** and generic `OPENAI_API_KEY` is not used.

Once those two dedicated environment variables are configured:

```bash
inquiry-triage --provider openai --allow-live \
  --input data/local-smoke.json --out out-openai
```

For the API, after stopping any server using port 8000:

```bash
TRIAGE_ALLOW_LIVE=1 uvicorn inquiry_triage.api:create_live_app \
  --factory --host 127.0.0.1 --port 8000
```

The adapter implements **`POST https://api.openai.com/v1/responses`** via `httpx==0.28.1`: non-streaming JSON, strict `text.format` schema, `store: false`, and explicit refusal/completion checks. It does not implement Azure, Chat Completions, arbitrary compatible hosts, or tools. Documentation basis: [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs) and [Responses migration](https://developers.openai.com/api/docs/guides/migrate-to-responses), reviewed 2026-10-27.

Outer deadline: 30 seconds; HTTP connect timeout: 5 seconds; other phase timeouts: 25 seconds. Retries are owned only by the processing core. A timed-out remote request might still incur cost; retrying can incur another charge. `store: false` is not a claim of zero data retention. **No live OpenAI inference or model-quality evaluation is claimed.**

## Rules, failures, and limitations

Scoring is a business heuristic: backend integration **+40**, explicit USD budget ≥5,000 **+35**, explicit timeline 14–90 days inclusive **+25**. Qualification requires a score ≥70 and no unknown/missing field. Complete sample lead-001 scores 100; the incomplete sample scores 40 and goes to review. Scores are not model confidence.

Required nullable fields prevent omitted facts from acquiring defaults. Instructions request facts only, but schema validation cannot prove factual grounding or prevent all hallucinations/prompt injection. Human review remains necessary.

| Situation | Policy |
| --- | --- |
| Missing fields, unknown service, score below 70 | Review with reasons |
| Invalid JSON/schema, timeout, connection failure | Bounded retry, then review |
| HTTP 429/5xx | Bounded retry; `Retry-After` defers directly to review |
| Nonretryable 4xx, refusal/incomplete output | Review without repeated attempts |
| Repeated normalized text | Duplicate of the first record; no second extraction |
| Duplicate input IDs | Reject batch before extraction |
| Missing provider configuration | Fail immediately; never fall back to fixtures |

Retries default to two attempts (core cap: three); delays are exponential starting at 0.5 seconds for HTTP adapters, 0.001 seconds for fixtures. Fixture deadlines are deliberately 20ms to exercise failures quickly. Programmer errors fail loudly instead of being disguised as successful extraction.

Deduplication is case/whitespace-normalized exact text and lasts **one batch/request**. Repeated requests are processed again; there is no durable idempotency. A duplicate of a failed record points to that failed original. Processing is sequential within each batch.

Logs omit input text, credentials, provider error bodies and extracted contact details; they include event names, short fingerprints, status, attempts and score. Fingerprints are not guaranteed anonymization. No persistent queue, authentication, rate limiting, database, durable telemetry, review UI or transactional external sink is included. Keep the demo on localhost.

## Troubleshooting

| Symptom | Action |
| --- | --- |
| `inquiry-triage: command not found` | Activate `.venv`; ensure editable install completed. Try `python -m inquiry_triage.cli`. |
| Editable install unsupported | Install `requirements.lock` first; it upgrades the venv's pip. |
| `data/...` not found | Run commands from the repository root. |
| Unknown fixture text goes to review | Expected: fixtures are lookups, not a language model. Use a real provider explicitly for new text. |
| Port already in use | Stop the earlier server, or use another port consistently in server and curl commands. |
| Ollama connection/404/model error | Verify the daemon's loopback address and `ollama list`; the demo never auto-downloads or falls back. |
| Ollama incomplete/timeout | Cold load or output budget may be insufficient; inspect review reasons. Limits are currently code settings. |
| OpenAI opt-in/config error | Both dedicated variables and `--allow-live` are required (API: separate factory and `TRIAGE_ALLOW_LIVE=1`). |
| A key was added to `.env.example` | Remove it before staging. Keep the template blank; `.env*` files are ignored except `.env.example`. |

## Repository map and integration notes

- `src/inquiry_triage/`: typed models, shared core, CLI/API, provider adapters, configuration.
- `data/`: synthetic inquiries, fixtures, expected artifacts and two-record local sample.
- `tests/`: workflow, adapter protocol/error handling, configuration and export regressions.
- `scripts/http_smoke.py`: temporary localhost HTTP verification.
- `docs/walkthrough.md`: short portfolio walkthrough.

An existing Python backend can call the same core behind its own route or worker. The adapters demonstrate request/validation boundaries; real model quality still needs evaluation. Durable idempotency and review storage belong in the host backend. A real CRM client would require its own authorization and review policy; this demo only writes local JSON.

All example organizations, names and inquiries are synthetic. No employer code, real customer data, customer outcomes, production benchmarks, or delivery-time promises are presented.
