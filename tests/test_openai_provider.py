import asyncio
import json
import logging

import httpx
import pytest
from fastapi.testclient import TestClient

from inquiry_triage.api import create_app, create_live_app
from inquiry_triage.config import provider_mode, select_provider
from inquiry_triage.core import process
from inquiry_triage.models import Inquiry
from inquiry_triage.openai_provider import ENDPOINT, OUTPUT_SCHEMA, OpenAIProvider

FIELDS = dict(
    company="Synthetic Harbor",
    contact_email="demo@example.com",
    budget_usd=8000,
    timeline_days=30,
    service="backend_integration",
)


def envelope(fields=None):
    return {
        "id": "resp_synthetic",
        "object": "response",
        "status": "completed",
        "error": None,
        "output": [
            {"type": "reasoning", "summary": []},
            {
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [
                    {
                        "type": "output_text",
                        "text": json.dumps(FIELDS if fields is None else fields),
                        "annotations": [],
                    }
                ],
            },
        ],
    }


def run_responses(responses, *, records=None, timeout=1):
    requests = []

    def handler(request):
        requests.append(request)
        item = responses[min(len(requests) - 1, len(responses) - 1)]
        if isinstance(item, Exception):
            raise item
        return item

    provider = OpenAIProvider(
        api_key="synthetic-secret", model="test-model", transport=httpx.MockTransport(handler)
    )
    results = asyncio.run(
        process(
            records or [Inquiry(id="x", text="Synthetic private text")],
            provider,
            retry_delay=0,
            timeout=timeout,
        )
    )
    return results, requests


def test_request_contract_and_parse():
    results, requests = run_responses([httpx.Response(200, json=envelope())])
    assert len(requests) == 1
    request = requests[0]
    assert str(request.url) == ENDPOINT and request.method == "POST"
    assert request.headers["authorization"] == "Bearer synthetic-secret"
    body = json.loads(request.content)
    assert body["model"] == "test-model"
    assert body["store"] is False and body["stream"] is False
    assert body["input"] == [{"role": "user", "content": "Synthetic private text"}]
    assert "Missing or ambiguous" in body["instructions"]
    assert body["text"]["format"] == dict(
        type="json_schema", name="inquiry", strict=True, schema=OUTPUT_SCHEMA
    )
    assert set(OUTPUT_SCHEMA["required"]) == set(FIELDS)
    assert request.extensions["timeout"]["connect"] == 5
    assert request.extensions["timeout"]["read"] == 25
    assert results[0].status == "qualified" and results[0].fields.model_dump() == FIELDS


@pytest.mark.parametrize("status", [429, 500, 502, 503])
def test_transient_http_retries_then_recovers(status):
    results, calls = run_responses([httpx.Response(status), httpx.Response(200, json=envelope())])
    assert len(calls) == results[0].attempts == 2
    assert results[0].status == "qualified"


@pytest.mark.parametrize("status", [429, 500, 503])
def test_transient_http_exhausted(status):
    results, calls = run_responses([httpx.Response(status, text="synthetic-secret private body")])
    assert len(calls) == 2 and results[0].fields is None
    assert results[0].review_reasons == ["provider_http_" + str(status)]


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422, 302])
def test_nonretryable_status_and_no_redirect(status):
    results, calls = run_responses(
        [httpx.Response(status, headers={"location": "https://example.com"})]
    )
    assert len(calls) == 1
    assert results[0].review_reasons == ["provider_http_" + str(status)]


@pytest.mark.parametrize("status", [429, 503])
def test_retry_after_defers_to_review_instead_of_retrying_early(status):
    results, calls = run_responses([httpx.Response(status, headers={"retry-after": "120"})])
    assert len(calls) == 1 and results[0].status == "review"


@pytest.mark.parametrize(
    "exc,code",
    [
        (httpx.ReadTimeout("private"), "provider_timeout"),
        (httpx.ConnectError("private"), "provider_connection_error"),
    ],
)
def test_transport_failure_is_bounded(exc, code):
    results, calls = run_responses([exc])
    assert len(calls) == 2 and results[0].review_reasons == [code]


def test_mock_http_cancellation_is_real():
    cancelled = []

    async def slow(request):
        try:
            await asyncio.sleep(60)
        finally:
            cancelled.append(True)

    provider = OpenAIProvider(
        api_key="synthetic", model="test", transport=httpx.MockTransport(slow)
    )
    results = asyncio.run(
        process([Inquiry(id="x", text="slow")], provider, timeout=0.001, retry_delay=0)
    )
    assert len(cancelled) == results[0].attempts == 2
    assert results[0].review_reasons == ["provider_timeout"]


def test_model_refusal_is_not_retried():
    body = envelope()
    body["output"][1]["content"] = [{"type": "refusal", "refusal": "private refusal text"}]
    results, calls = run_responses([httpx.Response(200, json=body)])
    assert len(calls) == 1 and results[0].review_reasons == ["model_refusal"]


@pytest.mark.parametrize(
    "body",
    [
        None,
        [],
        {"status": "completed", "output": []},
        {
            "status": "completed",
            "output": [{"type": "message", "role": "assistant", "content": "bad"}],
        },
        envelope([]),
        envelope({"budget_usd": "invented"}),
        envelope({**FIELDS, "company": "   "}),
    ],
)
def test_invalid_output_is_retried_then_reviewed(body):
    results, calls = run_responses([httpx.Response(200, json=body)])
    assert len(calls) == 2 and results[0].review_reasons == ["invalid_output"]
    assert results[0].fields is None


def test_invalid_json_text_and_invalid_http_json():
    body = envelope()
    body["output"][1]["content"][0]["text"] = "{bad"
    for response in [httpx.Response(200, text="not json"), httpx.Response(200, json=body)]:
        results, calls = run_responses([response])
        assert len(calls) == 2 and results[0].review_reasons == ["invalid_output"]


@pytest.mark.parametrize(
    "status,code", [("incomplete", "provider_incomplete"), ("failed", "provider_response_failed")]
)
def test_noncompleted_response_never_qualifies(status, code):
    body = envelope()
    body["status"] = status
    results, calls = run_responses([httpx.Response(200, json=body)])
    assert len(calls) == 1 and results[0].review_reasons == [code]


def test_nulls_are_preserved_and_reviewed():
    fields = dict(
        company=None, contact_email=None, budget_usd=None, timeline_days=None, service="unknown"
    )
    results, _ = run_responses([httpx.Response(200, json=envelope(fields))])
    assert results[0].fields.model_dump() == fields
    assert len([r for r in results[0].review_reasons if r.startswith("missing:")]) == 5


def test_company_named_unknown_is_not_missing():
    results, _ = run_responses(
        [httpx.Response(200, json=envelope({**FIELDS, "company": "unknown"}))]
    )
    assert results[0].status == "qualified"


def test_duplicate_not_extracted_or_qualified_twice():
    results, calls = run_responses(
        [httpx.Response(200, json=envelope())],
        records=[Inquiry(id="1", text=" same "), Inquiry(id="2", text="SAME")],
    )
    assert len(calls) == 1
    assert [r.status for r in results] == ["qualified", "duplicate"]
    assert len([r for r in results if r.status == "qualified"]) == 1


def test_no_sensitive_logs(caplog):
    caplog.set_level(logging.DEBUG)
    results, _ = run_responses(
        [httpx.Response(401, text="synthetic-secret Synthetic private text")]
    )
    assert results[0].status == "review"
    for private in ["synthetic-secret", "Synthetic private text"]:
        assert private not in caplog.text


def test_live_requires_explicit_config_and_default_factory_stays_offline(monkeypatch):
    with pytest.raises(ValueError, match="opt-in"):
        select_provider("openai")
    with pytest.raises(ValueError, match="key and model"):
        select_provider("openai", allow_live=True, env={})
    with pytest.raises(ValueError, match="allow_live"):
        OpenAIProvider(api_key="synthetic", model="test")
    with pytest.raises(ValueError, match="Provider must"):
        select_provider("typo")
    # Synthetic settings only: no real keys are read by this test.
    monkeypatch.setenv("TRIAGE_OPENAI_API_KEY", "synthetic")
    monkeypatch.setenv("TRIAGE_OPENAI_MODEL", "test")
    monkeypatch.setenv("TRIAGE_ALLOW_LIVE", "1")
    with TestClient(create_app()) as client:
        assert client.get("/health").json()["mode"] == "offline_fixture"
    monkeypatch.setenv("TRIAGE_ALLOW_LIVE", "0")
    with pytest.raises(ValueError, match="opt-in"):
        create_live_app()
    adapter = select_provider(
        "openai",
        allow_live=True,
        env={"TRIAGE_OPENAI_API_KEY": "synthetic", "TRIAGE_OPENAI_MODEL": "test"},
    )
    assert provider_mode(adapter) == "openai_live"  # construction only; no extract() call


def test_fastapi_mock_http_mode_never_claims_live():
    provider = OpenAIProvider(
        api_key="synthetic",
        model="test",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=envelope())),
    )
    with TestClient(create_app(provider)) as client:
        response = client.post("/triage", json={"inquiries": [{"id": "x", "text": "synthetic"}]})
        assert response.status_code == 200
        assert response.json()["mode"] == "openai_mock_http"
        assert response.json()["results"][0]["status"] == "qualified"


def test_transport_timeout_recovers_without_fallback():
    results, calls = run_responses(
        [httpx.ReadTimeout("synthetic"), httpx.Response(200, json=envelope())]
    )
    assert len(calls) == 2 and results[0].status == "qualified"


def test_invalid_output_can_recover():
    results, calls = run_responses(
        [httpx.Response(200, json=envelope({})), httpx.Response(200, json=envelope())]
    )
    assert len(calls) == 2 and results[0].status == "qualified"


def test_core_retry_limit_three_and_invalid_configuration():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(503)

    provider = OpenAIProvider(
        api_key="synthetic", model="test", transport=httpx.MockTransport(handler)
    )
    records = [Inquiry(id="x", text="synthetic")]
    result = asyncio.run(process(records, provider, max_attempts=3, retry_delay=0))[0]
    assert result.attempts == len(calls) == 3
    with pytest.raises(ValueError):
        asyncio.run(process(records, provider, max_attempts=4))


def test_cli_opt_in_errors_leave_no_artifacts(tmp_path):
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    for flags in [["--provider", "openai"], ["--provider", "openai", "--allow-live"]]:
        result = subprocess.run(
            [sys.executable, "-m", "inquiry_triage.cli", *flags, "--out", str(tmp_path)],
            cwd=root,
            env={},
            capture_output=True,
            text=True,
        )
        assert result.returncode == 2
        assert not list(tmp_path.iterdir())
        assert "Traceback" not in result.stderr


def test_duplicate_of_failed_record_stays_out_of_sink():
    results, calls = run_responses(
        [httpx.Response(401)],
        records=[Inquiry(id="1", text="same"), Inquiry(id="2", text=" SAME ")],
    )
    assert len(calls) == 1 and results[1].duplicate_of == "1"
    assert [r.status for r in results] == ["review", "duplicate"]
    assert not [r for r in results if r.status == "qualified"]
