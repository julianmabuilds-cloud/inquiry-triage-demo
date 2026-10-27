import argparse
import asyncio
import csv
import json
import logging
from pathlib import Path

from .core import process, summary
from .models import Batch
from .config import processing_options, provider_mode, select_provider


def main() -> None:
    parser = argparse.ArgumentParser(description="Inquiry triage (default: offline fixtures)")
    parser.add_argument("--input", type=Path, default=Path("data/inquiries.json"))
    parser.add_argument("--fixtures", type=Path, default=Path("data/fixtures.json"))
    parser.add_argument("--out", type=Path, default=Path("out"))
    parser.add_argument("--provider", choices=["fixture", "openai", "ollama"], default="fixture")
    parser.add_argument(
        "--allow-live", action="store_true", help="Explicitly allow paid OpenAI requests"
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    batch = Batch.model_validate_json(args.input.read_text())
    try:
        provider = select_provider(
            args.provider, allow_live=args.allow_live, fixtures=args.fixtures
        )
    except ValueError as exc:
        parser.error(str(exc))
    mode = provider_mode(provider)
    results = asyncio.run(process(batch.inquiries, provider, **processing_options(provider)))
    args.out.mkdir(parents=True, exist_ok=True)
    records = [r.model_dump() for r in results]
    for name, payload in [
        ("run.json", {"mode": mode, "summary": summary(results)}),
        ("results.json", records),
        ("review.json", [r for r in records if r["status"] == "review"]),
        (
            "mock_crm.json",
            {"mock_only": True, "records": [r for r in records if r["status"] == "qualified"]},
        ),
    ]:
        (args.out / name).write_text(json.dumps(payload, indent=2) + "\n")
    with (args.out / "results.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "id",
                "status",
                "score",
                "attempts",
                "duplicate_of",
                "review_reasons",
                "company",
                "contact_email",
                "budget_usd",
                "timeline_days",
                "service",
            ],
        )
        writer.writeheader()
        for r in records:
            flat = {**r, **(r["fields"] or {})}
            row = {k: flat.get(k) for k in (writer.fieldnames or [])}
            row["review_reasons"] = ";".join(r["review_reasons"])
            # Prevent formula injection when opened in a spreadsheet.
            for key, value in row.items():
                if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
                    row[key] = "'" + value
            writer.writerow(row)
    print(json.dumps({"mode": mode, **summary(results)}, sort_keys=True))


if __name__ == "__main__":
    main()
