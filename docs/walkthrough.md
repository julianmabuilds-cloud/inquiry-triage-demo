# Two-minute demo walkthrough

1. **State the scope (15 seconds).** “This is an offline Python workflow demo. Extraction responses are fixtures, not real LLM results. It shows how I would structure an integration boundary and handle failures.”
2. **Show the synthetic inputs (20 seconds).** Open `data/inquiries.json`. Point out the complete inquiry, incomplete inquiry, and repeated text. The special failure behavior lives in `fixtures.json`, not in business routing rules.
3. **Run the batch (30 seconds).** Run `inquiry-triage --out out`. Show two qualified, five review, one duplicate, and ten provider attempts. Logs show an initial timeout recovered by retry and two exhausted failures.
4. **Inspect decisions (30 seconds).** Open `out/results.json`. Show lead-002's null fields, lead-003's second attempt, lead-004's invalid output, lead-005's timeout, and lead-007's reference to lead-001. Open `out/mock_crm.json` and emphasize that it is local JSON only.
5. **Show the same core behind HTTP (15 seconds).** Start the README's Uvicorn command and post the same JSON with curl. Explain that deduplication is scoped to the request.
6. **Close with evidence and limits (10 seconds).** Run `pytest -q`. “These tests validate workflow behavior with synthetic fixtures. A real OpenAI Responses adapter exists, but only mock HTTP tests have run. Factual extraction evaluation, persistent idempotency, and authentication remain future work.”

## Optional adapter code walkthrough (no paid calls)

Open `src/inquiry_triage/openai_provider.py`: show the fixed `/v1/responses` URL, strict `text.format` schema, input instructions, transport timeouts and refusal parsing. Run `pytest -q tests/test_openai_provider.py` to demonstrate failure routing with `MockTransport`. Point out `openai_mock_http` in the API test and `run.json` for CLI mode provenance. Show `.env.example` with blank values and the separate opt-in entry points without entering a key or running live mode. Finish with `mypy src`.

Say explicitly: “This proves adapter wiring and local failure policy against synthetic HTTP responses. We have not sent a live model request or measured real extraction quality.”

## Optional local model walkthrough

Show the small synthetic input and manually authored expectations in `data/local-smoke*.json`. Explain the explicit Ollama CLI/API commands in README. Inspect `ollama list` on the demonstration machine before choosing a model; the repository assumes none is installed. Run `pytest -q tests/test_ollama_provider.py` for mock HTTP evidence. Clearly label any future real inference separately; the checked-in tests do not establish model accuracy. Nothing automatically downloads models.
