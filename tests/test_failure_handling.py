from app.services import processor
from app.services.pdf import render_certificate as real_render
from tests.conftest import JOBS_URL, job_payload, make_recipients


def _fail_for(name: str, exc: Exception):
    def render(data):
        if data.recipient_name == name:
            raise exc
        return real_render(data)

    return render


def test_one_rendering_failure_does_not_stop_the_others(client, monkeypatch):
    monkeypatch.setattr(processor, "render_certificate", _fail_for("Person 2", RuntimeError("boom")))

    job_id = client.post(JOBS_URL, json=job_payload(make_recipients(5))).json()["id"]
    status = client.get(f"{JOBS_URL}/{job_id}").json()

    assert status["status"] == "completed_with_errors"
    assert (status["generated"], status["failed"], status["pending"]) == (4, 1, 0)
    (failure,) = status["failures"]
    assert failure["recipient_name"] == "Person 2"
    assert failure["position"] == 2
    assert "RuntimeError: boom" in failure["error"]

    # the four good ones are really there
    generated = client.get(f"{JOBS_URL}/{job_id}/certificates", params={"status": "generated"}).json()
    assert generated["total"] == 4
    assert all(client.get(c["download_url"]).status_code == 200 for c in generated["items"])


def test_storage_failure_is_also_isolated(client, monkeypatch):
    storage = client.app.state.storage
    real_save = storage.save

    def flaky_save(job_id, cert_id, data):
        if flaky_save.calls == 0:
            flaky_save.calls += 1
            raise OSError("disk full")
        return real_save(job_id, cert_id, data)

    flaky_save.calls = 0
    monkeypatch.setattr(storage, "save", flaky_save)

    status = client.get(
        f"{JOBS_URL}/{client.post(JOBS_URL, json=job_payload(make_recipients(3))).json()['id']}"
    ).json()
    assert (status["generated"], status["failed"]) == (2, 1)
    assert "disk full" in status["failures"][0]["error"]


def test_job_fails_when_every_certificate_fails(client, monkeypatch):
    monkeypatch.setattr(processor, "render_certificate", lambda data: (_ for _ in ()).throw(ValueError("x")))
    body = client.post(JOBS_URL, json=job_payload(make_recipients(3))).json()
    status = client.get(f"{JOBS_URL}/{body['id']}").json()
    assert status["status"] == "failed"
    assert status["failed"] == 3
    assert status["links"]["download"] is None


def test_failed_certificate_cannot_be_downloaded(client, monkeypatch):
    monkeypatch.setattr(processor, "render_certificate", _fail_for("Person 0", RuntimeError("boom")))
    job_id = client.post(JOBS_URL, json=job_payload(make_recipients(2))).json()["id"]
    failed = client.get(f"{JOBS_URL}/{job_id}/certificates", params={"status": "failed"}).json()
    (item,) = failed["items"]
    assert item["download_url"] is None
    assert client.get(f"/api/v1/certificates/{item['id']}/download").status_code == 409
