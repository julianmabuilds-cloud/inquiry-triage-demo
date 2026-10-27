"""Opt-in OpenAI Responses HTTP adapter. No environment or secret-file discovery."""

import json
from typing import Any, Dict, Optional

import httpx

from .provider import ProviderError
from .extraction_contract import INSTRUCTIONS, OUTPUT_SCHEMA

ENDPOINT = "https://api.openai.com/v1/responses"


class OpenAIProvider:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        allow_live: bool = False,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        if transport is None and not allow_live:
            raise ValueError("Live requests require explicit allow_live=True")
        if not api_key.strip() or not model.strip():
            raise ValueError("Explicit API key and model are required")
        self._api_key = api_key
        self.model = model
        self._transport = transport
        self.mode = "openai_mock_http" if transport is not None else "openai_live"

    async def extract(self, text: str, attempt: int) -> Dict[str, Any]:
        # One request per attempt. All retries are owned by the shared core.
        payload = {
            "model": self.model,
            "store": False,
            "stream": False,
            "instructions": INSTRUCTIONS,
            "input": [{"role": "user", "content": text}],
            "max_output_tokens": 2048,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "inquiry",
                    "strict": True,
                    "schema": OUTPUT_SCHEMA,
                }
            },
        }
        try:
            async with httpx.AsyncClient(
                transport=self._transport,
                trust_env=False,
                follow_redirects=False,
                timeout=httpx.Timeout(25.0, connect=5.0),
            ) as client:
                response = await client.post(
                    ENDPOINT, headers={"Authorization": "Bearer " + self._api_key}, json=payload
                )
        except httpx.TimeoutException:
            raise ProviderError("provider_timeout", retryable=True) from None
        except httpx.TransportError:
            raise ProviderError("provider_connection_error", retryable=True) from None
        status = response.status_code
        if status == 429 or 500 <= status <= 599:
            # Do not expose error bodies. Do not retry earlier than a server-directed delay:
            # queue for human/manual rerun instead of sleeping without a bound.
            raise ProviderError(
                "provider_http_" + str(status), retryable="retry-after" not in response.headers
            )
        if status != 200:
            raise ProviderError("provider_http_" + str(status), retryable=False)
        try:
            body = response.json()
        except ValueError:
            raise ProviderError("invalid_output", retryable=True) from None
        return parse_response(body)


def parse_response(body: Any) -> Dict[str, Any]:
    if not isinstance(body, dict):
        raise ProviderError("invalid_output", retryable=True)
    if body.get("status") == "incomplete":
        raise ProviderError("provider_incomplete", retryable=False)
    if body.get("status") != "completed" or body.get("error") is not None:
        raise ProviderError("provider_response_failed", retryable=False)
    output = body.get("output")
    if not isinstance(output, list):
        raise ProviderError("invalid_output", retryable=True)
    texts = []
    for item in output:
        if not isinstance(item, dict):
            raise ProviderError("invalid_output", retryable=True)
        if item.get("type") != "message":
            continue  # Responses may include reasoning items before the assistant message.
        if item.get("role") != "assistant" or not isinstance(item.get("content"), list):
            raise ProviderError("invalid_output", retryable=True)
        for part in item["content"]:
            if not isinstance(part, dict):
                raise ProviderError("invalid_output", retryable=True)
            if part.get("type") == "refusal":
                raise ProviderError("model_refusal", retryable=False)
            if part.get("type") != "output_text" or not isinstance(part.get("text"), str):
                raise ProviderError("invalid_output", retryable=True)
            texts.append(part["text"])
    # This adapter supports a single JSON text payload, not multiple messages/tools.
    if len(texts) != 1:
        raise ProviderError("invalid_output", retryable=True)
    try:
        fields = json.loads(texts[0])
    except ValueError:
        raise ProviderError("invalid_output", retryable=True) from None
    if not isinstance(fields, dict):
        raise ProviderError("invalid_output", retryable=True)
    return fields
