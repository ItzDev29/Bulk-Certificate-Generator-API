import enum
import uuid
from datetime import date, datetime

from sqlalchemy import (
    Date,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, UTCDateTime, utcnow


class JobStatus(str, enum.Enum):
    PENDING = "pending"  # accepted, waiting for a worker
    PROCESSING = "processing"  # a worker is generating certificates
    COMPLETED = "completed"  # every recipient succeeded
    COMPLETED_WITH_ERRORS = "completed_with_errors"  # some succeeded, some failed
    FAILED = "failed"  # nothing could be generated

    @property
    def is_finished(self) -> bool:
        return self in FINISHED_JOB_STATUSES


FINISHED_JOB_STATUSES = frozenset(
    {JobStatus.COMPLETED, JobStatus.COMPLETED_WITH_ERRORS, JobStatus.FAILED}
)


class CertificateStatus(str, enum.Enum):
    PENDING = "pending"
    GENERATED = "generated"
    FAILED = "failed"


def _enum_column(enum_cls: type[enum.Enum]) -> Enum:
    return Enum(
        enum_cls,
        native_enum=False,
        length=32,
        values_callable=lambda e: [m.value for m in e],
        validate_strings=True,
    )


def _new_id() -> str:
    return str(uuid.uuid4())


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    status: Mapped[JobStatus] = mapped_column(
        _enum_column(JobStatus), default=JobStatus.PENDING, index=True
    )

    # Certificate information shared by every recipient in the job
    certificate_title: Mapped[str] = mapped_column(String(80))
    event_name: Mapped[str] = mapped_column(String(150))
    issued_by: Mapped[str] = mapped_column(String(100))
    issue_date: Mapped[date] = mapped_column(Date)

    total: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    certificates: Mapped[list["Certificate"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="Certificate.position"
    )


class Certificate(Base):
    """One row per recipient in a request (valid or not), so failures are traceable."""

    __tablename__ = "certificates"
    __table_args__ = (
        UniqueConstraint("job_id", "position", name="uq_certificate_job_position"),
        Index("ix_certificates_job_status", "job_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    # Index of the recipient in the submitted list, so clients can map results back.
    position: Mapped[int] = mapped_column(Integer)

    recipient_name: Mapped[str | None] = mapped_column(String(255))
    recipient_email: Mapped[str | None] = mapped_column(String(255))
    achievement: Mapped[str | None] = mapped_column(String(150))

    status: Mapped[CertificateStatus] = mapped_column(
        _enum_column(CertificateStatus), default=CertificateStatus.PENDING
    )
    error: Mapped[str | None] = mapped_column(Text)
    file_path: Mapped[str | None] = mapped_column(String(512))  # relative to STORAGE_DIR
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    generated_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    job: Mapped[Job] = relationship(back_populates="certificates")
