import time

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

JOBS_URL = "/api/v1/certificate-jobs"


def make_settings(tmp_path, **overrides) -> Settings:
    values = dict(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        storage_dir=tmp_path / "certs",
        processing_mode="inline",  # jobs finish before the HTTP response returns
        recover_jobs_on_startup=False,
    )
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.fixture
def settings(tmp_path):
    return make_settings(tmp_path)


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as c:
        yield c


def job_payload(recipients=None, **overrides) -> dict:
    payload = {
        "event_name": "Advanced Python Bootcamp",
        "issued_by": "Acme Academy",
        "issue_date": "2026-10-01",
        "recipients": recipients
        if recipients is not None
        else [
            {"name": "Asha Patil", "email": "asha@example.com"},
            {"name": "Ravi Kumar", "email": "ravi@example.com", "achievement": "with Distinction"},
        ],
    }
    payload.update(overrides)
    return payload


def make_recipients(n: int) -> list[dict]:
    return [{"name": f"Person {i}", "email": f"person{i}@example.com"} for i in range(n)]


def wait_for_job(client, job_id: str, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f"{JOBS_URL}/{job_id}").json()
        if body["status"] in ("completed", "completed_with_errors", "failed"):
            return body
        time.sleep(0.05)
    raise AssertionError(f"Job {job_id} did not finish in {timeout}s")
