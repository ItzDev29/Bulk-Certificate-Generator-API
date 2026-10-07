import io
from datetime import date

from pypdf import PdfReader

from app.services.pdf import CertificateData, render_certificate
from app.services.storage import CertificateStorage
from tests.conftest import JOBS_URL, job_payload


def make_data(**overrides) -> CertificateData:
    values = dict(
        certificate_id="11111111-2222-3333-4444-555555555555",
        title="Certificate of Completion",
        recipient_name="Asha Patil",
        event_name="Advanced Python Bootcamp",
        issued_by="Acme Academy",
        issue_date=date(2026, 10, 1),
    )
    values.update(overrides)
    return CertificateData(**values)


def pdf_text(data: bytes) -> str:
    reader = PdfReader(io.BytesIO(data))
    assert len(reader.pages) == 1
    return reader.pages[0].extract_text()


def test_pdf_contains_recipient_specific_information():
    pdf = render_certificate(make_data(achievement="with Distinction"))

    assert pdf.startswith(b"%PDF")
    text = pdf_text(pdf)
    for expected in [
        "CERTIFICATE OF COMPLETION",
        "Asha Patil",
        "Advanced Python Bootcamp",
        "with Distinction",
        "Acme Academy",
        "01 October 2026",
        "11111111-2222-3333-4444-555555555555",
    ]:
        assert expected in text


def test_different_recipients_get_different_certificates():
    a = pdf_text(render_certificate(make_data(recipient_name="Asha Patil")))
    b = pdf_text(render_certificate(make_data(recipient_name="Ravi Kumar")))
    assert "Asha Patil" in a and "Ravi Kumar" not in a
    assert "Ravi Kumar" in b


def test_very_long_text_still_renders_on_one_page():
    pdf = render_certificate(
        make_data(recipient_name="W" * 80, event_name="Long Event Name " * 9, achievement="A" * 150)
    )
    assert pdf_text(pdf)


def test_job_generates_one_pdf_file_per_recipient(client, settings):
    job_id = client.post(JOBS_URL, json=job_payload()).json()["id"]

    files = sorted((settings.storage_dir / job_id).glob("*.pdf"))
    assert len(files) == 2
    assert all(f.read_bytes().startswith(b"%PDF") for f in files)
    texts = [pdf_text(f.read_bytes()) for f in files]
    assert any("Asha Patil" in t for t in texts) and any("Ravi Kumar" in t for t in texts)
    assert not list((settings.storage_dir / job_id).glob("*.tmp"))


def test_storage_refuses_paths_outside_root(tmp_path):
    storage = CertificateStorage(tmp_path / "root")
    storage.ensure_root()
    try:
        storage.resolve("../outside.pdf")
    except ValueError:
        pass
    else:
        raise AssertionError("path traversal was not blocked")
