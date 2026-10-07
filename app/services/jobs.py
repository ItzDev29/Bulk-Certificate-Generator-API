"""Job creation, recipient validation and read-side queries."""

import logging
from collections import Counter
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import utcnow
from app.models import Certificate, CertificateStatus, Job, JobStatus
from app.schemas import JobCreate, Recipient

logger = logging.getLogger(__name__)

MAX_ERROR_LENGTH = 500


def format_validation_error(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"]) or "recipient"
        msg = err["msg"].removeprefix("Value error, ")
        parts.append(f"{loc}: {msg}")
    return "; ".join(parts)[:MAX_ERROR_LENGTH]


def _best_effort_str(value: Any, limit: int = 255) -> str | None:
    """Keep whatever the client sent for an invalid recipient, so the failure is identifiable."""
    if value is None:
        return None
    return str(value)[:limit]


def create_job(session: Session, payload: JobCreate) -> Job:
    """Validate every recipient and persist the job (not yet committed).

    Invalid recipients become FAILED certificates immediately; valid ones are PENDING
    and will be picked up by the worker."""
    job = Job(
        certificate_title=payload.certificate_title,
        event_name=payload.event_name,
        issued_by=payload.issued_by,
        issue_date=payload.issue_date,
        total=len(payload.recipients),
    )
    seen_emails: set[str] = set()

    for position, raw in enumerate(payload.recipients):
        cert = Certificate(position=position, status=CertificateStatus.PENDING)
        try:
            recipient = Recipient.model_validate(raw)
        except ValidationError as exc:
            cert.status = CertificateStatus.FAILED
            cert.error = format_validation_error(exc)
            cert.recipient_name = _best_effort_str(raw.get("name"))
            cert.recipient_email = _best_effort_str(raw.get("email"))
        else:
            cert.recipient_name = recipient.name
            cert.recipient_email = str(recipient.email)
            cert.achievement = recipient.achievement
            key = cert.recipient_email.lower()
            if key in seen_emails:
                cert.status = CertificateStatus.FAILED
                cert.error = "email: duplicate recipient in this request"
            else:
                seen_emails.add(key)
        job.certificates.append(cert)

    if not any(c.status == CertificateStatus.PENDING for c in job.certificates):
        # Nothing to generate: the job is finished the moment it is created.
        job.status = JobStatus.FAILED
        job.completed_at = utcnow()

    session.add(job)
    return job


def certificate_counts(session: Session, job_id: str) -> dict[CertificateStatus, int]:
    rows = session.execute(
        select(Certificate.status, func.count())
        .where(Certificate.job_id == job_id)
        .group_by(Certificate.status)
    ).all()
    counts = Counter({status: n for status, n in rows})
    return {s: counts.get(s, 0) for s in CertificateStatus}


def final_status(counts: dict[CertificateStatus, int]) -> JobStatus:
    generated = counts[CertificateStatus.GENERATED]
    failed = counts[CertificateStatus.FAILED]
    if failed == 0:
        return JobStatus.COMPLETED
    return JobStatus.COMPLETED_WITH_ERRORS if generated else JobStatus.FAILED


def list_certificates(
    session: Session,
    job_id: str,
    status: CertificateStatus | None,
    limit: int,
    offset: int,
) -> tuple[int, list[Certificate]]:
    base = select(Certificate).where(Certificate.job_id == job_id)
    if status is not None:
        base = base.where(Certificate.status == status)
    total = session.scalar(select(func.count()).select_from(base.subquery())) or 0
    items = session.scalars(base.order_by(Certificate.position).limit(limit).offset(offset)).all()
    return total, list(items)
