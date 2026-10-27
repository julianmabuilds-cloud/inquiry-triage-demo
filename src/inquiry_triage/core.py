import asyncio
import hashlib
import logging
from collections import Counter
from typing import List, Tuple

from pydantic import ValidationError

from .models import Extracted, Inquiry, Result
from .provider import Provider, ProviderError, UnsupportedInput, normalize

logger = logging.getLogger("inquiry_triage")


def score(fields: Extracted) -> Tuple[int, List[str]]:
    reasons = []
    points = 0
    if fields.service == "backend_integration":
        points += 40
        reasons.append("backend_integration:+40")
    if fields.budget_usd is not None and fields.budget_usd >= 5000:
        points += 35
        reasons.append("budget_at_least_5000:+35")
    if fields.timeline_days is not None and 14 <= fields.timeline_days <= 90:
        points += 25
        reasons.append("timeline_14_to_90_days:+25")
    return points, reasons


async def process(
    inquiries: List[Inquiry],
    provider: Provider,
    timeout: float = 0.02,
    max_attempts: int = 2,
    retry_delay: float = 0.001,
) -> List[Result]:
    if timeout <= 0 or retry_delay < 0 or not 1 <= max_attempts <= 3:
        raise ValueError("timeout must be positive; max_attempts must be 1..3")
    ids = [item.id for item in inquiries]
    if len(ids) != len(set(ids)):
        raise ValueError("inquiry IDs must be unique within a batch")
    results: List[Result] = []
    seen: dict[str, str] = {}
    for inquiry in inquiries:
        fingerprint = hashlib.sha256(normalize(inquiry.text).encode()).hexdigest()
        if fingerprint in seen:
            results.append(
                Result(id=inquiry.id, status="duplicate", duplicate_of=seen[fingerprint])
            )
            logger.info("event=duplicate fingerprint=%s", fingerprint[:12])
            continue
        seen[fingerprint] = inquiry.id
        result = Result(id=inquiry.id, status="review")
        for attempt in range(1, max_attempts + 1):
            result.attempts = attempt
            try:
                raw = await asyncio.wait_for(provider.extract(inquiry.text, attempt), timeout)
                result.fields = Extracted.model_validate(raw)
                break
            except (asyncio.TimeoutError, ValidationError, ProviderError) as exc:
                code = (
                    exc.code
                    if isinstance(exc, ProviderError)
                    else (
                        "provider_timeout"
                        if isinstance(exc, asyncio.TimeoutError)
                        else "invalid_output"
                    )
                )
                logger.info(
                    "event=attempt_failed fingerprint=%s attempt=%d code=%s",
                    fingerprint[:12],
                    attempt,
                    code,
                )
                if attempt == max_attempts or (
                    isinstance(exc, ProviderError) and not exc.retryable
                ):
                    result.review_reasons.append(code)
                    break
                else:
                    await asyncio.sleep(retry_delay * (2 ** (attempt - 1)))
            except UnsupportedInput:
                result.review_reasons.append("unsupported_fixture_input")
                break
        if result.fields is not None:
            result.score, result.score_reasons = score(result.fields)
            for name, value in result.fields.model_dump().items():
                if value is None or (name == "service" and value == "unknown"):
                    result.review_reasons.append("missing:" + name)
            if result.score < 70:
                result.review_reasons.append("score_below_70")
            if not result.review_reasons:
                result.status = "qualified"
        logger.info(
            "event=processed fingerprint=%s status=%s attempts=%d score=%d",
            fingerprint[:12],
            result.status,
            result.attempts,
            result.score,
        )
        results.append(result)
    return results


def summary(results: List[Result]) -> dict:
    counts = Counter(item.status for item in results)
    return {
        "total": len(results),
        "qualified": counts["qualified"],
        "review": counts["review"],
        "duplicate": counts["duplicate"],
        "provider_attempts": sum(r.attempts for r in results),
    }
