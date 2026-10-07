import os
import re
from pathlib import Path


class CertificateStorage:
    """Stores generated PDFs on local disk, grouped by job.

    The DB keeps only a path relative to the root, so the root can move (or this
    class can be swapped for S3/GCS) without touching stored rows."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def ensure_root(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, job_id: str, certificate_id: str, data: bytes) -> str:
        rel = Path(job_id) / f"{certificate_id}.pdf"
        target = self.root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".pdf.tmp")
        tmp.write_bytes(data)
        os.replace(tmp, target)  # atomic: readers never see a half-written file
        return rel.as_posix()

    def resolve(self, relative_path: str) -> Path:
        root = self.root.resolve()
        path = (root / relative_path).resolve()
        if not path.is_relative_to(root):
            raise ValueError("Path escapes storage root")
        return path


def slugify(text: str | None, fallback: str = "certificate") -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", text or "").strip("-").lower()
    return slug or fallback
