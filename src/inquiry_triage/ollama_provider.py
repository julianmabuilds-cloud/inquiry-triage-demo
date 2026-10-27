"""Local-only Ollama /api/chat adapter; never pulls models or follows redirects."""

import ipaddress
import json
from typing import Any, Dict, Optional
from urllib.parse import urlsplit

import httpx

from .extraction_contract import INSTRUCTIONS, OUTPUT_SCHEMA
from .provider import ProviderError


class OllamaProvider:
    def __init__(
        self, *, base_url: str, model: str, transport: Optional[httpx.AsyncBaseTransport] = None
    ) -> None:
        parsed = urlsplit(base_url)
        try:
            local = ipaddress.ip_address(parsed.hostname or "").is_loopback
            port = parsed.port
        except ValueError:
            local = False
            port = None
        if (
            parsed.scheme != "http"
            or not local
            or port is None
            or parsed.username
            or parsed.password
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Ollama URL must be an explicit HTTP loopback IP and port")
        if not model.strip() or "cloud" in model.casefold() or "/" in model or "://" in model:
            raise ValueError("Choose an installed local model name, not a cloud model")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._transport = transport
        self.mode = "ollama_mock_http" if transport is not None else "ollama_local"

    async def extract(self, text: str, attempt: int) -> Dict[str, Any]:
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": INSTRUCTIONS + " Return JSON matching: " + json.dumps(OUTPUT_SCHEMA),
                },
                {"role": "user", "content": text},
            ],
            "stream": False,
            "format": OUTPUT_SCHEMA,
            "options": {"temperature": 0, "seed": 42, "num_predict": 512, "num_ctx": 4096},
            "keep_alive": "0",
        }
        try:
            async with httpx.AsyncClient(
                transport=self._transport,
                trust_env=False,
                follow_redirects=False,
                timeout=httpx.Timeout(55.0, connect=3.0),
            ) as client:
                # Verify model metadata locally before sending any inquiry text. Reject remote aliases.
                info = await client.post(self.base_url + "/api/show", json={"model": self.model})
                self._check_http(info)
                metadata = info.json()
                if not isinstance(metadata, dict) or not metadata.get("details"):
                    raise ProviderError("invalid_model_metadata", retryable=False)
                if metadata.get("remote_host") or metadata.get("remote_model"):
                    raise ProviderError("remote_model_forbidden", retryable=False)
                thinking = metadata.get("thinking")
                if isinstance(thinking, dict) and False in thinking.get("values", []):
                    payload["think"] = False
                response = await client.post(self.base_url + "/api/chat", json=payload)
        except httpx.TimeoutException:
            raise ProviderError("provider_timeout", retryable=True) from None
        except httpx.TransportError:
            raise ProviderError("provider_connection_error", retryable=True) from None
        except ValueError:
            raise ProviderError("invalid_output", retryable=True) from None
        self._check_http(response)
        try:
            body = response.json()
            if not isinstance(body, dict) or body.get("error"):
                raise ProviderError("invalid_output", retryable=True)
            if body.get("done") is not True or body.get("done_reason") != "stop":
                raise ProviderError("provider_incomplete", retryable=False)
            message = body.get("message")
            if not isinstance(message, dict) or message.get("role") != "assistant":
                raise ProviderError("invalid_output", retryable=True)
            content = message.get("content")
            if not isinstance(content, str):
                raise ProviderError("invalid_output", retryable=True)
            fields = json.loads(content)
            if not isinstance(fields, dict):
                raise ProviderError("invalid_output", retryable=True)
            return fields
        except ValueError:
            raise ProviderError("invalid_output", retryable=True) from None

    @staticmethod
    def _check_http(response: httpx.Response) -> None:
        if response.status_code != 200:
            retry = response.status_code == 429 or response.status_code >= 500
            raise ProviderError(
                "provider_http_" + str(response.status_code),
                retryable=retry and "retry-after" not in response.headers,
            )
