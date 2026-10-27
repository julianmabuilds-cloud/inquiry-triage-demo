from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Inquiry(StrictModel):
    id: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=1, max_length=10000)


class Extracted(StrictModel):
    # Required keys, nullable values: omitted information is never defaulted into a fact.
    company: Optional[str] = Field(min_length=1, max_length=200)
    contact_email: Optional[str] = Field(pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    budget_usd: Optional[int] = Field(ge=0, le=100000000)
    timeline_days: Optional[int] = Field(ge=0, le=3650)
    service: Literal["backend_integration", "other", "unknown"]

    @field_validator("company")
    @classmethod
    def nonblank_company(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and not value.strip():
            raise ValueError("company must not be whitespace-only")
        return value


class Result(StrictModel):
    id: str
    status: Literal["qualified", "review", "duplicate"]
    fields: Optional[Extracted] = None
    score: int = 0
    score_reasons: List[str] = Field(default_factory=list)
    review_reasons: List[str] = Field(default_factory=list)
    duplicate_of: Optional[str] = None
    attempts: int = 0


class Batch(StrictModel):
    inquiries: List[Inquiry] = Field(min_length=1, max_length=100)
