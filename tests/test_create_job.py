from tests.conftest import JOBS_URL, job_payload, make_recipients


def test_create_job_returns_202_with_location_and_counts(client):
    resp = client.post(JOBS_URL, json=job_payload())

    assert resp.status_code == 202
    body = resp.json()
    assert body["total"] == 2
    assert body["event_name"] == "Advanced Python Bootcamp"
    assert resp.headers["Location"].endswith(f"{JOBS_URL}/{body['id']}")
    assert body["links"]["certificates"].endswith(f"{JOBS_URL}/{body['id']}/certificates")


def test_job_is_persisted_and_retrievable(client):
    job_id = client.post(JOBS_URL, json=job_payload()).json()["id"]
    resp = client.get(f"{JOBS_URL}/{job_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == job_id


def test_defaults_are_applied(client):
    payload = job_payload()
    del payload["issue_date"]
    body = client.post(JOBS_URL, json=payload).json()
    assert body["certificate_title"] == "Certificate of Completion"
    assert body["issue_date"]  # defaults to today


def test_unknown_job_returns_404(client):
    assert client.get(f"{JOBS_URL}/6f1c5a0e-2b1d-4c5e-9d0a-0123456789ab").status_code == 404


def test_malformed_job_id_returns_422(client):
    assert client.get(f"{JOBS_URL}/not-a-uuid").status_code == 422


def test_bulk_request_in_a_single_call(client):
    body = client.post(JOBS_URL, json=job_payload(make_recipients(200))).json()
    assert body["total"] == 200
    assert body["generated"] == 200
