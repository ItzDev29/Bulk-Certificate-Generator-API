"""Background processing of certificate jobs.

Design: the HTTP request only validates and persists the job, then hands the job id
to a thread pool. The worker generates certificates one by one; each certificate is
committed on its own, so (a) one failure never affects the others and (b) clients
polling the status endpoint see live progress."""

import logging
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from app.database import utcnow
from app.models import Certificate, CertificateStatus, Job, JobStatus
from app.services import pdf
from app.services.jobs import MAX_ERROR_LENGTH, certificate_counts, final_status
from app.services.storage import CertificateStorage

logger = logging.getLogger(__name__)

# Referenced via the module attribute so tests can monkeypatch it.
render_certificate = pdf.render_certificate


def _generate_one(session: Session, storage: CertificateStorage, job: Job, cert: Certificate) -> None:
    """Generate a single certificate. Never raises for per-certificate problems."""
    try:
        data = pdf.CertificateData(
            certificate_id=cert.id,
            title=job.certificate_title,
            recipient_name=cert.recipient_name or "",
            event_name=job.event_name,
            issued_by=job.issued_by,
            issue_date=job.issue_date,
            achievement=cert.achievement,
        )
        content = render_certificate(data)
        cert.file_path = storage.save(job.id, cert.id, content)
        cert.status = CertificateStatus.GENERATED
        cert.generated_at = utcnow()
        cert.error = None
    except Exception as exc:  # noqa: BLE001 - isolate *any* failure to this certificate
        logger.exception("Certificate %s (job %s) failed", cert.id, job.id)
        cert.status = CertificateStatus.FAILED
        cert.error = f"{type(exc).__name__}: {exc}"[:MAX_ERROR_LENGTH]
        cert.file_path = None
    session.commit()


def process_job(session_factory: sessionmaker, storage: CertificateStorage, job_id: str) -> None:
    with session_factory() as session:
        # Atomic claim: if two workers race for the same job, only one wins.
        claimed = session.execute(
            update(Job)
            .where(Job.id == job_id, Job.status == JobStatus.PENDING)
            .values(status=JobStatus.PROCESSING, started_at=utcnow())
        ).rowcount
        session.commit()
        if not claimed:
            logger.info("Job %s not pending (already claimed or finished); skipping", job_id)
            return

        job = session.get(Job, job_id)
        pending = session.scalars(
            select(Certificate)
            .where(Certificate.job_id == job_id, Certificate.status == CertificateStatus.PENDING)
            .order_by(Certificate.position)
        ).all()
        for cert in pending:
            _generate_one(session, storage, job, cert)

        job.status = final_status(certificate_counts(session, job_id))
        job.completed_at = utcnow()
        session.commit()
        logger.info("Job %s finished: %s", job_id, job.status.value)


class JobDispatcher:
    def __init__(
        self,
        session_factory: sessionmaker,
        storage: CertificateStorage,
        workers: int = 4,
        inline: bool = False,
    ):
        self.session_factory = session_factory
        self.storage = storage
        self.inline = inline
        self._executor = None if inline else ThreadPoolExecutor(workers, thread_name_prefix="certgen")

    def process(self, job_id: str) -> None:
        try:
            process_job(self.session_factory, self.storage, job_id)
        except Exception:  # noqa: BLE001
            # The job stays PROCESSING; startup recovery will re-queue it.
            logger.exception("Job %s crashed unexpectedly", job_id)

    def submit(self, job_id: str) -> None:
        if self._executor is None:
            self.process(job_id)
        else:
            self._executor.submit(self.process, job_id)

    def recover(self) -> int:
        """Re-queue jobs left unfinished by a previous run (crash / restart).

        Assumes a single app process owns the queue (see README)."""
        with self.session_factory() as session:
            session.execute(
                update(Job).where(Job.status == JobStatus.PROCESSING).values(status=JobStatus.PENDING)
            )
            session.commit()
            ids = session.scalars(select(Job.id).where(Job.status == JobStatus.PENDING)).all()
        for job_id in ids:
            self.submit(job_id)
        return len(ids)

    def shutdown(self) -> None:
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)
