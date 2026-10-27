import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from inquiry_triage.api import create_app
from inquiry_triage.core import process, score, summary
from inquiry_triage.models import Batch, Extracted, Inquiry
from inquiry_triage.provider import FixtureProvider

ROOT = Path(__file__).resolve().parents[1]


def provider():
    return FixtureProvider(ROOT / "data/fixtures.json")


def batch():
    return Batch.model_validate_json((ROOT / "data/inquiries.json").read_text())


def test_end_to_end_expected_results():
    results = asyncio.run(process(batch().inquiries, provider()))
    assert [r.model_dump() for r in results] == json.loads(
        (ROOT / "data/expected/results.json").read_text()
    )
    assert summary(results) == dict(
        total=8, qualified=2, review=5, duplicate=1, provider_attempts=10
    )
    assert results[1].fields.budget_usd is None
    assert "missing:budget_usd" in results[1].review_reasons
    assert results[2].attempts == 2 and results[2].status == "qualified"
    assert results[3].review_reasons == ["invalid_output"]
    assert results[4].review_reasons == ["provider_timeout"]
    assert results[6].duplicate_of == "lead-001" and results[6].attempts == 0
    assert results[7].review_reasons == ["unsupported_fixture_input"]


@pytest.mark.parametrize(
    "budget,days,expected",
    [
        (4999, 14, 65),
        (5000, 14, 100),
        (5000, 90, 100),
        (5000, 91, 75),
        (5000, 13, 75),
        (None, None, 40),
    ],
)
def test_score_boundaries(budget, days, expected):
    fields = Extracted(
        company=None,
        contact_email=None,
        budget_usd=budget,
        timeline_days=days,
        service="backend_integration",
    )
    assert score(fields)[0] == expected


@pytest.mark.parametrize(
    "changes",
    [
        {"budget_usd": "8000"},
        {"budget_usd": -1},
        {"contact_email": "bad"},
        {"service": "made_up"},
        {"extra": "bad"},
        {"budget_usd": True},
    ],
)
def test_strict_schema(changes):
    raw = dict(
        company=None, contact_email=None, budget_usd=None, timeline_days=None, service="unknown"
    )
    raw.update(changes)
    with pytest.raises(ValidationError):
        Extracted.model_validate(raw)


def test_missing_keys_rejected():
    with pytest.raises(ValidationError):
        Extracted.model_validate({})


def test_api_valid_and_invalid_input():
    with TestClient(create_app(provider())) as client:
        assert client.get("/health").json()["mode"] == "offline_fixture"
        response = client.post("/triage", json=batch().model_dump())
        assert response.status_code == 200
        assert response.json()["summary"]["review"] == 5
        assert response.json()["results"] == json.loads(
            (ROOT / "data/expected/results.json").read_text()
        )
        for payload in [
            {"inquiries": []},
            {"inquiries": [{"id": "x", "text": ""}]},
            {"inquiries": [{"id": "x", "text": "t"}] * 101},
        ]:
            assert client.post("/triage", json=payload).status_code == 422
        assert (
            client.post(
                "/triage", json={"inquiries": [{"id": "x", "text": "a"}, {"id": "x", "text": "b"}]}
            ).status_code
            == 422
        )


def test_cli_artifacts(tmp_path):
    completed = subprocess.run(
        [sys.executable, "-m", "inquiry_triage.cli", "--out", str(tmp_path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(completed.stdout)["provider_attempts"] == 10
    for name in ["results.json", "review.json", "mock_crm.json", "results.csv"]:
        assert (tmp_path / name).read_text() == (ROOT / "data/expected" / name).read_text()
    assert "alex@example.com" not in completed.stderr
    assert "event=attempt_failed" in completed.stderr


def test_deadline_cancels_provider_and_bounds_attempts():
    class Slow:
        calls = 0
        cancelled = 0

        async def extract(self, text, attempt):
            self.calls += 1
            try:
                await asyncio.sleep(60)
            finally:
                self.cancelled += 1

    slow = Slow()
    result = asyncio.run(process([Inquiry(id="x", text="slow")], slow, timeout=0.001))[0]
    assert slow.calls == slow.cancelled == result.attempts == 2
    assert result.review_reasons == ["provider_timeout"]


def test_dedupe_is_batch_scoped():
    first = batch().inquiries[0]
    for _ in range(2):
        assert asyncio.run(process([first], provider()))[0].status == "qualified"


def test_duplicate_ids_fail_before_provider():
    first = batch().inquiries[0]
    with pytest.raises(ValueError, match="IDs must be unique"):
        asyncio.run(process([first, first], provider()))


def test_unknown_service_requires_review():
    class Unknown:
        async def extract(self, text, attempt):
            return dict(
                company="Synthetic",
                contact_email="demo@example.com",
                budget_usd=9000,
                timeline_days=30,
                service="unknown",
            )

    result = asyncio.run(process([Inquiry(id="x", text="unknown")], Unknown()))[0]
    assert result.status == "review"
    assert "missing:service" in result.review_reasons


def test_csv_formula_prefix_is_escaped(tmp_path):
    import csv

    payload = batch().model_dump()
    payload["inquiries"][0]["id"] = "=1+1"
    input_path = tmp_path / "input.json"
    input_path.write_text(json.dumps(payload))
    subprocess.run(
        [
            sys.executable,
            "-m",
            "inquiry_triage.cli",
            "--input",
            str(input_path),
            "--out",
            str(tmp_path),
        ],
        cwd=ROOT,
        capture_output=True,
        check=True,
    )
    with (tmp_path / "results.csv").open() as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["id"] == "'=1+1"
    assert rows[6]["duplicate_of"] == "'=1+1"


def test_repeated_cli_export_does_not_append_mock_crm_records(tmp_path):
    for _ in range(2):
        subprocess.run(
            [sys.executable, "-m", "inquiry_triage.cli", "--out", str(tmp_path)],
            cwd=ROOT,
            capture_output=True,
            check=True,
        )
    sink = json.loads((tmp_path / "mock_crm.json").read_text())
    assert sink["mock_only"] is True
    assert [r["id"] for r in sink["records"]] == ["lead-001", "lead-003"]
    assert json.loads((tmp_path / "run.json").read_text())["mode"] == "offline_fixture"
