import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services.processor import JobDispatcher
from tests.conftest import JOBS_URL, job_payload, make_recipients, make_settings, wait_for_job


def test_finished_job_reports_full_progress(client):
    body = client.post(JOBS_URL, json=job_payload(make_recipients(5))).json()
    status = client.get(f"{JOBS_URL}/{body['id']}").json()

    assert status["status"] == "completed"
    assert (status["total"], status["generated"], status["failed"], status["pending"]) == (5, 5, 0, 0)
    assert status["progress_percent"] == 100.0
    assert status["started_at"] and status["completed_at"]
    assert status["failures"] == [] and status["failures_truncated"] is False
    assert status["links"]["download"]


def test_pending_job_reports_zero_progress_then_completes(client, monkeypatch):
    # Accept the job but don't run it, to observe the "queued" state.
    monkeypatch.setattr(JobDispatcher, "submit", lambda self, job_id: None)
    job_id = client.post(JOBS_URL, json=job_payload(make_recipients(4))).json()["id"]

    queued = client.get(f"{JOBS_URL}/{job_id}").json()
    assert queued["status"] == "pending"
    assert (queued["generated"], queued["pending"], queued["progress_percent"]) == (0, 4, 0.0)
    assert queued["links"]["download"] is None

    client.app.state.dispatcher.process(job_id)
    done = client.get(f"{JOBS_URL}/{job_id}").json()
    assert done["status"] == "completed" and done["progress_percent"] == 100.0


def test_progress_with_mixed_valid_and_invalid(client):
    recipients = make_recipients(3) + [{"name": "Bad", "email": "nope"}]
    status = client.get(
        f"{JOBS_URL}/{client.post(JOBS_URL, json=job_payload(recipients)).json()['id']}"
    ).json()
    assert (status["generated"], status["failed"], status["progress_percent"]) == (3, 1, 100.0)


def test_background_mode_processes_after_response(tmp_path):
    settings = make_settings(tmp_path, processing_mode="background")
    with TestClient(create_app(settings)) as client:
        resp = client.post(JOBS_URL, json=job_payload(make_recipients(25)))
        assert resp.status_code == 202
        final = wait_for_job(client, resp.json()["id"])
    assert final["status"] == "completed"
    assert final["generated"] == 25


def test_unfinished_jobs_are_recovered_on_startup(tmp_path, monkeypatch):
    # Simulate a crash: job accepted and marked "processing", but never finished.
    settings = make_settings(tmp_path)
    with TestClient(create_app(settings)) as first:
        monkeypatch.setattr(JobDispatcher, "submit", lambda self, job_id: None)
        job_id = first.post(JOBS_URL, json=job_payload(make_recipients(3))).json()["id"]
        from sqlalchemy import update
        from app.models import Job, JobStatus

        with first.app.state.session_factory() as s:
            s.execute(update(Job).values(status=JobStatus.PROCESSING))
            s.commit()
    monkeypatch.undo()

    restarted = make_settings(tmp_path, recover_jobs_on_startup=True)
    with TestClient(create_app(restarted)) as second:
        status = second.get(f"{JOBS_URL}/{job_id}").json()
    assert status["status"] == "completed" and status["generated"] == 3
