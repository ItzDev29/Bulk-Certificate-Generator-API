"""Pydantic schemas: request validation and response shapes."""

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.config import get_settings
from app.models import CertificateStatus, JobStatus


def ensure_renderable(value: str) -> str:
    """Text must be printable and drawable with the PDF's built-in (WinAnsi) font."""
    if not all(ch.isprintable() for ch in value):
        raise ValueError("contains control or non-printable characters")
    try:
        value.encode("cp1252")
    except UnicodeEncodeError as exc:
        bad = value[exc.start]
        raise ValueError(
            f"contains unsupported character {bad!r} (only Latin-script text is supported)"
        ) from None
    return value


# ----------------------------------------------------------------- requests


class Recipient(BaseModel):
    """One recipient. Validated per item (not by FastAPI) so that a single bad
    recipient is reported as a failed certificate instead of rejecting the whole job."""

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=80)
    email: EmailStr
    achievement: str | None = Field(
        default=None, max_length=150, description='Optional line, e.g. "with Distinction"'
    )

    @field_validator("achievement", mode="before")
    @classmethod
    def _blank_to_none(cls, v: Any) -> Any:
        return None if isinstance(v, str) and not v.strip() else v

    @field_validator("name", "achievement")
    @classmethod
    def _renderable(cls, v: str | None) -> str | None:
        return None if v is None else ensure_renderable(v)


class JobCreate(BaseModel):
    model_config = ConfigDict(
        str_strip_whitespace=True,
        json_schema_extra={
            "example": {
                "event_name": "Advanced Python Bootcamp",
                "issued_by": "Acme Academy",
                "issue_date": "2026-10-01",
                "recipients": [
                    {"name": "Asha Patil", "email": "asha@example.com"},
                    {
                        "name": "Ravi Kumar",
                        "email": "ravi@example.com",
                        "achievement": "with Distinction",
                    },
                ],
            }
        },
    )

    event_name: str = Field(min_length=1, max_length=150)
    issued_by: str = Field(min_length=1, max_length=100)
    issue_date: date = Field(default_factory=date.today)
    certificate_title: str = Field(default="Certificate of Completion", min_length=1, max_length=80)
    recipients: list[dict[str, Any]] = Field(
        min_length=1,
        max_length=get_settings().max_recipients_per_job,
        description="Each item: {name, email, achievement?}. Invalid items are recorded "
        "as failed certificates; they do not reject the request.",
    )

    @field_validator("event_name", "issued_by", "certificate_title")
    @classmethod
    def _renderable(cls, v: str) -> str:
        return ensure_renderable(v)


# ---------------------------------------------------------------- responses


class JobLinks(BaseModel):
    self: str
    certificates: str
    download: str | None = Field(None, description="ZIP of all certificates; set once finished")


class FailureOut(BaseModel):
    certificate_id: str
    position: int
    recipient_name: str | None
    recipient_email: str | None
    error: str | None


class JobOut(BaseModel):
    id: str
    status: JobStatus
    event_name: str
    issued_by: str
    certificate_title: str
    issue_date: date
    total: int
    generated: int
    failed: int
    pending: int
    progress_percent: float
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    links: JobLinks


class JobDetail(JobOut):
    failures: list[FailureOut] = Field(description="First failures (capped); use the "
                                       "certificates endpoint with ?status=failed for all")
    failures_truncated: bool


class CertificateOut(BaseModel):
    id: str
    position: int
    recipient_name: str | None
    recipient_email: str | None
    status: CertificateStatus
    error: str | None
    generated_at: datetime | None
    download_url: str | None


class CertificatePage(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[CertificateOut]
