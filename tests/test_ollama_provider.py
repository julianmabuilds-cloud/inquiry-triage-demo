import asyncio
import json
import logging

import httpx
import pytest
from fastapi.testclient import TestClient

from inquiry_triage import api, cli
from inquiry_triage.config import processing_options, select_provider
from inquiry_triage.core import process
from inquiry_triage.extraction_contract import OUTPUT_SCHEMA
from inquiry_triage.models import Inquiry
from inquiry_triage.ollama_provider import OllamaProvider

FIELDS = dict(
    company=None,
    contact_email=None,
    budget_usd=None,
    timeline_days=None,
    service="backend_integration",
)


def response(fields=FIELDS):
    return {
        "model": "llama-test",
        "done": True,
        "done_reason": "stop",
        "message": {"role": "assistant", "content": json.dumps(fields)},
    }


def make_provider(items=None, metadata=None):
    calls = []
    chat_calls = []
    items = items or [httpx.Response(200, json=response())]

    def handler(request):
        calls.append(request)
        if request.url.path == "/api/show":
            return httpx.Response(
                200,
                json=metadata
                if metadata is not None
                else {"details": {"family": "llama", "format": "gguf"}},
            )
        assert request.url.path == "/api/chat"
        chat_calls.append(request)
        item = items[min(len(chat_calls) - 1, len(items) - 1)]
        if isinstance(item, Exception):
            raise item
        return item

    provider = OllamaProvider(
        base_url="http://127.0.0.1:11434",
        model="llama-test",
        transport=httpx.MockTransport(handler),
    )
    return provider, calls, chat_calls


def run(provider):
    return asyncio.run(
        process(
            [Inquiry(id="x", text="Private synthetic inquiry")],
            provider,
            timeout=0.2,
            retry_delay=0,
        )
    )[0]


def test_request_and_nulls():
    provider, calls, chats = make_provider()
    result = run(provider)
    assert len(calls) == 2 and len(chats) == 1
    request = chats[0]
    body = json.loads(request.content)
    assert request.url.host == "127.0.0.1" and "authorization" not in request.headers
    assert body["model"] == "llama-test" and body["stream"] is False
    assert body["format"] == OUTPUT_SCHEMA
    assert body["options"] == dict(temperature=0, seed=42, num_predict=512, num_ctx=4096)
    assert body["keep_alive"] == "0"
    assert body["messages"][1]["content"] == "Private synthetic inquiry"
    assert result.status == "review" and result.fields.model_dump() == FIELDS
    assert "missing:budget_usd" in result.review_reasons
    assert processing_options(provider)["timeout"] == 60


@pytest.mark.parametrize(
    "url",
    [
        "https://api.ollama.com",
        "http://example.com:11434",
        "http://localhost:11434",
        "http://192.168.1.2:11434",
        "http://127.0.0.1:11434/api",
        "http://user:pass@127.0.0.1:11434",
        "http://127.0.0.1:11434?x=y",
    ],
)
def test_only_explicit_loopback_origin(url):
    with pytest.raises(ValueError):
        OllamaProvider(base_url=url, model="llama-test")


@pytest.mark.parametrize("model", ["", "qwen-cloud", "https://example.com/model", "org/model"])
def test_reject_remote_or_blank_model_names(model):
    with pytest.raises(ValueError):
        OllamaProvider(base_url="http://127.0.0.1:11434", model=model)


def test_remote_metadata_blocks_inquiry_before_chat():
    provider, calls, chats = make_provider(
        metadata={"details": {"family": "llama"}, "remote_host": "https://example.com"}
    )
    result = run(provider)
    assert result.review_reasons == ["remote_model_forbidden"]
    assert len(calls) == 1 and not chats
    assert b"Private synthetic inquiry" not in calls[0].content


@pytest.mark.parametrize(
    "body",
    [
        response({}),
        response([]),
        {"done": True, "done_reason": "stop"},
        {**response(), "message": {"role": "assistant", "content": "bad"}},
    ],
)
def test_invalid_output_is_bounded(body):
    provider, _, chats = make_provider([httpx.Response(200, json=body)])
    result = run(provider)
    assert len(chats) == 2 and result.review_reasons == ["invalid_output"]


def test_incomplete_is_not_qualified():
    provider, _, chats = make_provider(
        [httpx.Response(200, json={**response(), "done_reason": "length"})]
    )
    assert run(provider).review_reasons == ["provider_incomplete"]
    assert len(chats) == 1


@pytest.mark.parametrize("status,attempts", [(404, 1), (401, 1), (302, 1), (429, 2), (503, 2)])
def test_http_errors(status, attempts):
    provider, _, chats = make_provider([httpx.Response(status, text="private")])
    result = run(provider)
    assert len(chats) == attempts and result.review_reasons == [f"provider_http_{status}"]


def test_transport_timeout_and_recovery():
    provider, _, chats = make_provider(
        [httpx.ReadTimeout("private"), httpx.Response(200, json=response())]
    )
    result = run(provider)
    assert len(chats) == result.attempts == 2 and result.fields is not None


def test_actual_async_cancellation():
    cancellations = []

    async def slow(request):
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"details": {"family": "llama"}})
        try:
            await asyncio.sleep(60)
        finally:
            cancellations.append(True)

    provider = OllamaProvider(
        base_url="http://127.0.0.1:11434", model="llama-test", transport=httpx.MockTransport(slow)
    )
    result = asyncio.run(
        process([Inquiry(id="x", text="slow")], provider, timeout=0.001, retry_delay=0)
    )[0]
    assert len(cancellations) == 2 and result.review_reasons == ["provider_timeout"]


def test_config_is_explicit():
    with pytest.raises(ValueError):
        select_provider("ollama", env={})
    provider = select_provider(
        "ollama",
        env={"TRIAGE_OLLAMA_URL": "http://127.0.0.1:11434", "TRIAGE_OLLAMA_MODEL": "llama-test"},
    )
    assert provider.mode == "ollama_local"  # construction only


def test_api_factory_and_cli_mock_http(monkeypatch, tmp_path):
    provider, _, _ = make_provider()
    monkeypatch.setattr(api, "select_provider", lambda name: provider)
    with TestClient(api.create_local_app()) as client:
        result = client.post("/triage", json={"inquiries": [{"id": "x", "text": "synthetic"}]})
        assert result.status_code == 200 and result.json()["mode"] == "ollama_mock_http"
    input_path = tmp_path / "input.json"
    input_path.write_text(json.dumps({"inquiries": [{"id": "x", "text": "synthetic"}]}))
    monkeypatch.setattr(cli, "select_provider", lambda *args, **kwargs: provider)
    monkeypatch.setattr(
        "sys.argv",
        [
            "inquiry-triage",
            "--provider",
            "ollama",
            "--input",
            str(input_path),
            "--out",
            str(tmp_path),
        ],
    )
    cli.main()
    assert json.loads((tmp_path / "run.json").read_text())["mode"] == "ollama_mock_http"
    assert not json.loads((tmp_path / "mock_crm.json").read_text())["records"]


def test_sensitive_text_not_logged(caplog):
    caplog.set_level(logging.DEBUG)
    provider, _, _ = make_provider([httpx.Response(503, text="Private synthetic inquiry")])
    run(provider)
    assert "Private synthetic inquiry" not in caplog.text


def test_disable_thinking_only_when_metadata_supports_it():
    provider, _, chats = make_provider(
        metadata={"details": {"family": "llama"}, "thinking": {"values": [False, True]}}
    )
    run(provider)
    assert json.loads(chats[0].content)["think"] is False
