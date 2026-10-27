"""Tests are entirely offline; accidental real HTTP transport use is a hard failure."""

import httpx
import pytest


@pytest.fixture(autouse=True)
def block_real_async_http(monkeypatch):
    async def blocked(*args, **kwargs):
        raise AssertionError("Real HTTP is forbidden in tests; use MockTransport")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", blocked)
