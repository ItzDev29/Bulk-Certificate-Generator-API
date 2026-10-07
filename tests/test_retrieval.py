import io
import zipfile

from sqlalchemy import update

from app.models import Job, JobStatus
from app.services.processor import JobDispatcher
from tests.conftest import JOBS_URL, job_payload, make_recipients


def _create(client, n=3, **kw):
    return client.post(JOBS_URL, json=job_payload(make_recipients(n), **kw)).json()["id"]


def test_download_single_certificate(client):
    job_id = _create(client, 2)
    item = client.get(f"{JOBS_URL}/{job_id}/certificates").json()["items"][0]

    resp = client.get(item["download_url"])
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert 'filename="certificate-person-0.pdf"' in resp.headers["content-disposition"]
    assert resp.content.startswith(b"%PDF")


def test_list_certificates_with_filter_and_pagination(client):
    recipients = make_recipients(5) + [{"name": "Bad", "email": "nope"}]
    job_id = client.post(JOBS_URL, json=job_payload(recipients)).json()["id"]
    url = f"{JOBS_URL}/{job_id}/certificates"

    everything = client.get(url).json()
    assert everything["total"] == 6
    assert [i["position"] for i in everything["items"]] == list(range(6))

    page = client.get(url, params={"limit": 2, "offset": 2}).json()
    assert [i["position"] for i in page["items"]] == [2, 3]
    assert page["total"] == 6

    failed = client.get(url, params={"status": "failed"}).json()
    assert failed["total"] == 1 and failed["items"][0]["recipient_name"] == "Bad"

    assert client.get(url, params={"status": "bogus"}).status_code == 422
    assert client.get(url, params={"limit": 0}).status_code == 422


def test_download_all_as_zip(client):
    recipients = make_recipients(3) + [{"name": "Bad", "email": "nope"}]
    job_id = client.post(JOBS_URL, json=job_payload(recipients)).json()["id"]

    resp = client.get(f"{JOBS_URL}/{job_id}/download")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        assert zf.namelist() == [
            "0001-person-0.pdf",
            "0002-person-1.pdf",
            "0003-person-2.pdf",
        ]  # failed recipients are not included
        assert all(zf.read(n).startswith(b"%PDF") for n in zf.namelist())


def test_zip_is_refused_until_job_finishes(client, monkeypatch):
    monkeypatch.setattr(JobDispatcher, "submit", lambda self, job_id: None)
    job_id = _create(client, 2)
    assert client.get(f"{JOBS_URL}/{job_id}/download").status_code == 409


def test_zip_is_refused_when_nothing_was_generated(client):
    job_id = client.post(JOBS_URL, json=job_payload([{"name": "X"}])).json()["id"]
    assert client.get(f"{JOBS_URL}/{job_id}/download").status_code == 409


def test_not_ready_certificate_returns_409(client, monkeypatch):
    monkeypatch.setattr(JobDispatcher, "submit", lambda self, job_id: None)
    job_id = _create(client, 1)
    cert_id = client.get(f"{JOBS_URL}/{job_id}/certificates").json()["items"][0]["id"]
    assert client.get(f"/api/v1/certificates/{cert_id}/download").status_code == 409


def test_unknown_certificate_and_job_return_404(client):
    unknown = "6f1c5a0e-2b1d-4c5e-9d0a-0123456789ab"
    assert client.get(f"/api/v1/certificates/{unknown}/download").status_code == 404
    assert client.get(f"{JOBS_URL}/{unknown}/certificates").status_code == 404
    assert client.get(f"{JOBS_URL}/{unknown}/download").status_code == 404


def test_missing_file_on_disk_returns_410(client, settings):
    job_id = _create(client, 1)
    item = client.get(f"{JOBS_URL}/{job_id}/certificates").json()["items"][0]
    next((settings.storage_dir / job_id).glob("*.pdf")).unlink()
    assert client.get(item["download_url"]).status_code == 410


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}
