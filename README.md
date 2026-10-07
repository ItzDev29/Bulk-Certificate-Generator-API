# Bulk Certificate Generator API

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-009688.svg)](https://fastapi.tiangolo.com)
[![SQLAlchemy 2](https://img.shields.io/badge/SQLAlchemy-2.0-red.svg)](https://www.sqlalchemy.org/)
[![Tests](https://img.shields.io/badge/pytest-50%20passed-brightgreen.svg)](https://docs.pytest.org/)

An asynchronous, high-throughput FastAPI backend that processes bulk certificate generation requests in the background. Submit up to 1,000 recipients in a single HTTP request, track live job progress, and download generated PDF certificates individually or as a `.zip` archive.

<!-- Place your header image/architecture diagram at docs/images/hero-banner.png -->
![Bulk Certificate Generator Banner](docs/images/hero-banner.png)

## ✨ Key Features

- ⚡ **Asynchronous Background Processing:** Accepts bulk jobs instantly (`202 Accepted`) and hands off PDF rendering to a managed background thread pool.
- 🛡️ **Two-Tiered Validation Strategy:**
  - **Request-Level:** Strict validation for global fields (missing event title, empty list, >1000 recipients).
  - **Recipient-Level:** Isolated per-recipient validation so a single typo doesn't reject a 1,000-row batch.
- 🔒 **Fault Tolerance & Reliability:** Individual `try/except` rendering loops commit each certificate independently, preventing cascading failures.
- 🔄 **Automatic Recovery:** Unfinished jobs are auto-recovered and resumed from the last pending record on app restart.
- 📄 **Dynamic PDF Templating:** Automatic font wrapping and text-scaling built using ReportLab.

---

## 🛠️ Tech Stack

- **Framework:** Python 3.11+, FastAPI, Pydantic v2
- **Database:** SQLAlchemy 2 (SQLite default; supports PostgreSQL/MySQL via `DATABASE_URL`)
- **PDF Engine:** ReportLab
- **Testing:** pytest (50+ unit & integration tests)

## Setup

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt  # runtime-only: requirements.txt
cp .env.example .env                 # optional; defaults work out of the box
```

## Run

```bash
uvicorn app.main:app --reload        # single process (see "Design decisions")
```

Interactive docs: http://127.0.0.1:8000/docs. Tables are created on startup; PDFs are
stored under `./data/certificates/<job_id>/`.

## Test

```bash
pytest
```

Tests use a temporary SQLite file and temp storage per test; no setup needed.

## API

<img width="2392" height="3002" alt="APIs" src="https://github.com/user-attachments/assets/194df05a-f31b-4774-aa02-db5269b98d9c" />


| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/certificate-jobs` | Submit a bulk request → `202` + job (with `Location` header) |
| GET | `/api/v1/certificate-jobs/{job_id}` | Status, counts, progress %, first failures |
| GET | `/api/v1/certificate-jobs/{job_id}/certificates?status=&limit=&offset=` | Per-recipient results (filter/paginate) |
| GET | `/api/v1/certificates/{certificate_id}/download` | One certificate (PDF) |
| GET | `/api/v1/certificate-jobs/{job_id}/download` | All generated certificates of a finished job (ZIP) |
| GET | `/health` | Liveness + DB check |

### Submit a request

```bash
curl -X POST http://127.0.0.1:8000/api/v1/certificate-jobs \
  -H "Content-Type: application/json" \
  -d '{
    "event_name": "Advanced Python Bootcamp",
    "issued_by": "Acme Academy",
    "issue_date": "2026-10-01",
    "recipients": [
      {"name": "Asha Patil", "email": "asha@example.com"},
      {"name": "Ravi Kumar", "email": "ravi@example.com", "achievement": "with Distinction"},
      {"name": "No Email"}
    ]
  }'
```
<img width="1950" height="1174" alt="Screenshot 2026-10-08 000953" src="https://github.com/user-attachments/assets/52410e7f-4bae-4876-bdbe-6b8f22de819f" />

Job-level fields: `event_name` (required), `issued_by` (required), `issue_date` (default today),
`certificate_title` (default "Certificate of Completion"). Per recipient: `name` (1–80 chars),
`email` (valid address), optional `achievement` (≤150 chars). Max 1000 recipients per request
(`MAX_RECIPIENTS_PER_JOB`).

Response (`202 Accepted`, abridged):

```json
{ "id": "3f0c…", "status": "pending", "total": 3, "generated": 0, "failed": 1,
  "pending": 2, "progress_percent": 33.3, "links": { "self": "…", "certificates": "…", "download": null } }
```

### Check progress

```bash
curl http://127.0.0.1:8000/api/v1/certificate-jobs/<job_id>
```

`status` is `pending → processing → completed | completed_with_errors | failed`.
`failures[]` lists failed recipients with their original `position` in your list and an `error`.
For all failures: `…/certificates?status=failed`.

<img width="1952" height="1184" alt="Screenshot 2026-10-08 001305" src="https://github.com/user-attachments/assets/7536e48b-395b-450c-ae17-9a4f5c1e8ea0" />

### Retrieve certificates

```bash
# list (each generated item has a download_url)
curl "http://127.0.0.1:8000/api/v1/certificate-jobs/<job_id>/certificates?status=generated"
# one PDF
curl -OJ http://127.0.0.1:8000/api/v1/certificates/<certificate_id>/download
# everything as a ZIP (only once the job has finished)
curl -OJ http://127.0.0.1:8000/api/v1/certificate-jobs/<job_id>/download
```
<img width="1978" height="1183" alt="Screenshot 2026-10-08 001611" src="https://github.com/user-attachments/assets/926e1f40-4469-4f95-84cb-4883944dbb10" />

Error codes: `404` unknown id, `409` not ready (certificate pending/failed, job unfinished or
nothing generated), `410` file missing from storage, `422` invalid request.

<img width="1976" height="1162" alt="Screenshot 2026-10-08 001804" src="https://github.com/user-attachments/assets/523da4c1-9ad0-4e55-ab77-9576614de3bd" />

## Design decisions

**Asynchronous processing (accept → background → poll).** A job can hold up to 1000
recipients; generating them inside the HTTP request would tie up a connection and risk
client/proxy timeouts. The POST validates and persists the job, returns `202`, and hands the
job id to an in-process `ThreadPoolExecutor`. I chose this over Celery/RQ because it needs no
broker and keeps setup to `pip install` + `uvicorn`, while the job/status/retrieval API is the
same one a real queue would sit behind (only `JobDispatcher` would change).

**Two levels of validation.**
- *Request level* (missing event name, empty list, >1000 recipients, bad date) → whole request
  rejected with `422`; nothing is stored.
- *Recipient level* (missing/invalid email, bad name, duplicate email in the request) → that
  recipient is stored as a `failed` certificate with a readable error and its list position;
  the valid recipients still proceed. Recipients are therefore validated in the service layer,
  not by FastAPI's schema, to avoid one typo rejecting a 1000-row upload.

**Failure isolation.** Each certificate is rendered, written, and committed on its own inside
a `try/except`; any exception (rendering, disk, …) marks only that certificate `failed`.
Job status is derived at the end: all ok → `completed`, mixed → `completed_with_errors`,
none ok → `failed`. Per-certificate commits also give live progress while a job runs.

**Data model.** `jobs` (shared certificate info, status, timestamps) and `certificates` (one
row per submitted recipient, valid or not, with status, error, relative file path, and
`position`). Counts are computed with a grouped query, so they can't drift from the rows.
Files are written to a temp name then atomically renamed; the DB stores relative paths only.

**Reliability.** A job is claimed with an atomic `UPDATE … WHERE status='pending'`, so it can't
be processed twice. On startup, unfinished jobs (e.g. after a crash) are re-queued; processing
only touches still-`pending` certificates, so it resumes rather than restarts.

**PDF.** ReportLab, one hard-coded landscape A4 template (border, title, name, event, optional
achievement, date, issuer, certificate ID). Long names/titles shrink or wrap to fit.

## Limitations / next steps

- **Single-process queue.** Run with one app process (no `--workers N`); the startup recovery
  assumes it owns the queue. For multiple workers or horizontal scale, swap `JobDispatcher`
  for Celery/RQ/arq and run workers separately.
- **Latin-script text only.** The built-in PDF fonts can't draw e.g. Devanagari, so such names
  are rejected at validation time with a clear error rather than rendered as boxes. To support
  them, register a Unicode TTF (e.g. Noto Sans) in `app/services/pdf.py` and relax
  `ensure_renderable` in `app/schemas.py`.
- No authentication, rate limiting, or idempotency keys; add them before exposing publicly.
- Schema is created with `create_all`; use Alembic migrations in production.
- Local-disk storage; swap `CertificateStorage` for S3/GCS if needed.

## 📂 Project Architecture

```text
app/
├── main.py              # App initialization, lifespan events & job recovery
├── config.py            # Environment settings (Pydantic settings)
├── database.py          # SQLAlchemy engine, session factory, UTC datetimes
├── models.py            # Database schemas (Job & Certificate models)
├── schemas.py           # Request validation & API response schemas
├── api/
│   └── routes.py        # API endpoint definitions
└── services/
    ├── jobs.py          # Business logic for job creation & queries
    ├── processor.py     # Worker thread pool, dispatcher & recovery loop
    ├── pdf.py           # ReportLab certificate rendering engine
    └── storage.py       # File system management for PDF storage
```

```mermaid
flowchart TD
    %% Node Definitions
    Client([Client / Postman])
    API[FastAPI POST /api/v1/certificate-jobs]
    ReqVal{Request Validation<br/>(Global Fields & Length)}
    DB[(Database<br/>SQLite / PostgreSQL)]
    Dispatcher[ThreadPoolExecutor<br/>Job Dispatcher]
    
    RecipLoop[Iterate Recipients]
    RecipVal{Recipient Validation<br/>(Email, Name, Script)}
    RenderPDF[ReportLab Engine<br/>Render PDF & Write to Disk]
    
    DBPending[(Store Certificate:<br/>'pending')]
    DBSuccess[(Store Certificate:<br/>'generated')]
    DBFailed[(Store Certificate:<br/>'failed')]
    
    JobStatus{Derive Job Status}
    StatusComp[status: 'completed']
    StatusErr[status: 'completed_with_errors']
    StatusFail[status: 'failed']

    %% Workflow Connections
    Client -->|1. Submit Job Payload| API
    API --> ReqVal
    
    ReqVal -->|Invalid | Reject[Return 422 Unprocessable Entity]
    ReqVal -->|Valid| DB
    DB -->|2. Persist Job & Recipients| Dispatcher
    API -->|3. Return 202 Accepted + Job ID| Client

    Dispatcher --> RecipLoop
    RecipLoop --> RecipVal

    RecipVal -->|Invalid Email/Script| DBFailed
    RecipVal -->|Valid Data| DBPending
    
    DBPending --> RenderPDF
    RenderPDF -->|Success| DBSuccess
    RenderPDF -->|Error/Exception| DBFailed

    DBSuccess --> JobStatus
    DBFailed --> JobStatus

    JobStatus -->|All Succeeded| StatusComp
    JobStatus -->|Some Failed| StatusErr
    JobStatus -->|All Failed| StatusFail

    Client -.->|4. Poll GET /api/v1/certificate-jobs/job_id| DB
    Client -.->|5. Download ZIP GET /api/v1/certificate-jobs/job_id/download| Storage[/Data Directory / Storage/]
```
