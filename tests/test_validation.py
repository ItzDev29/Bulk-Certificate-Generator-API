import pytest

from tests.conftest import JOBS_URL, job_payload, make_recipients

GOOD = {"name": "Asha Patil", "email": "asha@example.com"}


# ---- request-level validation: the whole request is rejected (422) ----------


@pytest.mark.parametrize(
    "overrides",
    [
        {"recipients": []},
        {"event_name": ""},
        {"event_name": "x" * 151},
        {"issued_by": "   "},
        {"issue_date": "not-a-date"},
        {"event_name": "Pythön \u0939\u093f\u0928\u094d\u0926\u0940"},  # unsupported script
        {"recipients": "nope"},
        {"recipients": ["not-an-object"]},
    ],
)
def test_invalid_request_is_rejected(client, overrides):
    resp = client.post(JOBS_URL, json=job_payload(**overrides))
    assert resp.status_code == 422


def test_missing_required_field_is_rejected(client):
    payload = job_payload()
    del payload["event_name"]
    assert client.post(JOBS_URL, json=payload).status_code == 422


def test_too_many_recipients_is_rejected(client):
    resp = client.post(JOBS_URL, json=job_payload(make_recipients(1001)))
    assert resp.status_code == 422


# ---- recipient-level validation: recorded as failures, job continues ----------


@pytest.mark.parametrize(
    "bad, error_fragment",
    [
        ({"email": "x@example.com"}, "name"),  # missing name
        ({"name": "X"}, "email"),  # missing email
        ({"name": "X", "email": "not-an-email"}, "email"),
        ({"name": "   ", "email": "x@example.com"}, "name"),
        ({"name": "A" * 81, "email": "x@example.com"}, "name"),
        ({"name": "Bad\x00Name", "email": "x@example.com"}, "non-printable"),
        ({"name": "\u0906\u0936\u093e", "email": "x@example.com"}, "unsupported character"),
        ({"name": 123, "email": "x@example.com"}, "name"),
    ],
)
def test_invalid_recipient_fails_without_blocking_valid_ones(client, bad, error_fragment):
    resp = client.post(JOBS_URL, json=job_payload([GOOD, bad]))
    assert resp.status_code == 202

    body = client.get(f"{JOBS_URL}/{resp.json()['id']}").json()
    assert body["status"] == "completed_with_errors"
    assert (body["generated"], body["failed"]) == (1, 1)

    (failure,) = body["failures"]
    assert failure["position"] == 1  # maps back to the client's list
    assert error_fragment in failure["error"]


def test_duplicate_email_is_flagged_case_insensitively(client):
    dup = {"name": "Asha Again", "email": "ASHA@example.com"}
    body = client.post(JOBS_URL, json=job_payload([GOOD, dup])).json()
    status = client.get(f"{JOBS_URL}/{body['id']}").json()
    assert status["failed"] == 1
    assert "duplicate" in status["failures"][0]["error"]


def test_all_recipients_invalid_marks_job_failed(client):
    body = client.post(JOBS_URL, json=job_payload([{"name": "X"}, {"email": "bad"}])).json()
    assert body["status"] == "failed"
    assert body["failed"] == 2
    assert body["completed_at"] is not None


def test_blank_achievement_is_treated_as_absent(client):
    recipient = {**GOOD, "achievement": "   "}
    body = client.post(JOBS_URL, json=job_payload([recipient])).json()
    assert body["status"] == "completed"
