import logging
import os
import tempfile
import uuid
import zipfile
from typing import Annotated, Iterator

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from app.models import Certificate, CertificateStatus, Job, JobStatus
from app.schemas import (
    CertificateOut,
    CertificatePage,
    FailureOut,
    JobCreate,
    JobDetail,
    JobLinks,
    JobOut,
)
from app.services import jobs as job_service
from app.services.processor import JobDispatcher
from app.services.storage import CertificateStorage, slugify

logger = logging.getLogger(__name__)
router = APIRouter()

MAX_FAILURES_IN_STATUS = 50


# ------------------------------------------------------------ dependencies


def get_session(request: Request) -> Iterator[Session]:
    with request.app.state.session_factory() as session:
        yield session


def get_dispatcher(request: Request) -> JobDispatcher:
    return request.app.state.dispatcher


def get_storage(request: Request) -> CertificateStorage:
    return request.app.state.storage


SessionDep = Annotated[Session, Depends(get_session)]
DispatcherDep = Annotated[JobDispatcher, Depends(get_dispatcher)]
StorageDep = Annotated[CertificateStorage, Depends(get_storage)]


# ----------------------------------------------------------------- helpers


def _get_job(session: Session, job_id: uuid.UUID) -> Job:
    job = session.get(Job, str(job_id))
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    return job


def _job_fields(request: Request, session: Session, job: Job) -> dict:
    counts = job_service.certificate_counts(session, job.id)
    generated = counts[CertificateStatus.GENERATED]
    failed = counts[CertificateStatus.FAILED]
    done = generated + failed
    can_download = job.status.is_finished and generated > 0
    return dict(
        id=job.id,
        status=job.status,
        event_name=job.event_name,
        issued_by=job.issued_by,
        certificate_title=job.certificate_title,
        issue_date=job.issue_date,
        total=job.total,
        generated=generated,
        failed=failed,
        pending=job.total - done,
        progress_percent=round(done / job.total * 100, 1) if job.total else 100.0,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        links=JobLinks(
            self=str(request.url_for("get_job", job_id=job.id)),
            certificates=str(request.url_for("list_job_certificates", job_id=job.id)),
            download=str(request.url_for("download_job_zip", job_id=job.id)) if can_download else None,
        ),
    )


def _certificate_out(request: Request, cert: Certificate) -> CertificateOut:
    ready = cert.status == CertificateStatus.GENERATED
    return CertificateOut(
        id=cert.id,
        position=cert.position,
        recipient_name=cert.recipient_name,
        recipient_email=cert.recipient_email,
        status=cert.status,
        error=cert.error,
        generated_at=cert.generated_at,
        download_url=str(request.url_for("download_certificate", certificate_id=cert.id))
        if ready
        else None,
    )


# --------------------------------------------------------------- endpoints


@router.get("/health", tags=["meta"])
def health(session: SessionDep) -> dict:
    session.execute(text("SELECT 1"))
    return {"status": "ok"}


@router.post(
    "/api/v1/certificate-jobs",
    response_model=JobOut,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["jobs"],
    summary="Submit a bulk certificate generation request",
)
def create_certificate_job(
    payload: JobCreate,
    request: Request,
    response: Response,
    session: SessionDep,
    dispatcher: DispatcherDep,
) -> JobOut:
    """Validates the recipients, stores the job and returns immediately (202).
    Certificates are generated in the background; poll the job to follow progress."""
    job = job_service.create_job(session, payload)
    session.commit()  # must be durable *before* a worker looks it up
    if job.status == JobStatus.PENDING:
        dispatcher.submit(job.id)
    session.refresh(job)  # inline mode may already have finished it

    response.headers["Location"] = str(request.url_for("get_job", job_id=job.id))
    return JobOut(**_job_fields(request, session, job))


@router.get(
    "/api/v1/certificate-jobs/{job_id}",
    response_model=JobDetail,
    tags=["jobs"],
    summary="Job status, progress and failures",
)
def get_job(job_id: uuid.UUID, request: Request, session: SessionDep) -> JobDetail:
    job = _get_job(session, job_id)
    fields = _job_fields(request, session, job)
    failed_rows = session.scalars(
        select(Certificate)
        .where(Certificate.job_id == job.id, Certificate.status == CertificateStatus.FAILED)
        .order_by(Certificate.position)
        .limit(MAX_FAILURES_IN_STATUS)
    ).all()
    failures = [
        FailureOut(
            certificate_id=c.id,
            position=c.position,
            recipient_name=c.recipient_name,
            recipient_email=c.recipient_email,
            error=c.error,
        )
        for c in failed_rows
    ]
    return JobDetail(
        **fields, failures=failures, failures_truncated=fields["failed"] > len(failures)
    )


@router.get(
    "/api/v1/certificate-jobs/{job_id}/certificates",
    response_model=CertificatePage,
    tags=["certificates"],
    summary="List a job's certificates (filter by status, paginated)",
)
def list_job_certificates(
    job_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    status_filter: Annotated[CertificateStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CertificatePage:
    job = _get_job(session, job_id)
    total, items = job_service.list_certificates(session, job.id, status_filter, limit, offset)
    return CertificatePage(
        total=total,
        limit=limit,
        offset=offset,
        items=[_certificate_out(request, c) for c in items],
    )


@router.get(
    "/api/v1/certificates/{certificate_id}/download",
    tags=["certificates"],
    summary="Download one certificate (PDF)",
    response_class=FileResponse,
)
def download_certificate(
    certificate_id: uuid.UUID, session: SessionDep, storage: StorageDep
) -> FileResponse:
    cert = session.get(Certificate, str(certificate_id))
    if cert is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Certificate not found")
    if cert.status != CertificateStatus.GENERATED or not cert.file_path:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"Certificate is not available (status: {cert.status.value})"
        )
    path = storage.resolve(cert.file_path)
    if not path.is_file():
        logger.error("Certificate %s is marked generated but %s is missing", cert.id, path)
        raise HTTPException(status.HTTP_410_GONE, "Certificate file is no longer available")
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=f"certificate-{slugify(cert.recipient_name)}.pdf",
    )


@router.get(
    "/api/v1/certificate-jobs/{job_id}/download",
    tags=["certificates"],
    summary="Download all generated certificates of a finished job (ZIP)",
    response_class=FileResponse,
)
def download_job_zip(job_id: uuid.UUID, session: SessionDep, storage: StorageDep) -> FileResponse:
    job = _get_job(session, job_id)
    if not job.status.is_finished:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"Job is still {job.status.value}; try again when it finishes"
        )
    certs = session.scalars(
        select(Certificate)
        .where(Certificate.job_id == job.id, Certificate.status == CertificateStatus.GENERATED)
        .order_by(Certificate.position)
    ).all()
    if not certs:
        raise HTTPException(status.HTTP_409_CONFLICT, "Job has no generated certificates")

    tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            for cert in certs:
                path = storage.resolve(cert.file_path)
                if not path.is_file():
                    logger.error("Missing file for certificate %s; skipped in ZIP", cert.id)
                    continue
                zf.write(path, f"{cert.position + 1:04d}-{slugify(cert.recipient_name)}.pdf")
    except Exception:
        tmp.close()
        os.unlink(tmp.name)
        raise
    tmp.close()
    return FileResponse(
        tmp.name,
        media_type="application/zip",
        filename=f"certificates-{job.id}.zip",
        background=BackgroundTask(os.unlink, tmp.name),  # clean up after sending
    )
