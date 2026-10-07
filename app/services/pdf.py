"""Certificate rendering. One fixed template, drawn with ReportLab."""

import io
from dataclasses import dataclass
from datetime import date

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.utils import simpleSplit
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

PAGE_W, PAGE_H = landscape(A4)
NAVY = colors.HexColor("#1b2a49")
GOLD = colors.HexColor("#b8923a")
GREY = colors.HexColor("#555555")
CONTENT_W = PAGE_W - 200  # text area inside the border


@dataclass(frozen=True)
class CertificateData:
    certificate_id: str
    title: str
    recipient_name: str
    event_name: str
    issued_by: str
    issue_date: date
    achievement: str | None = None


def _fit(text: str, font: str, max_size: float, min_size: float, width: float):
    """Largest single-line size that fits; otherwise wrap at min_size."""
    size = max_size
    while size >= min_size:
        if stringWidth(text, font, size) <= width:
            return size, [text]
        size -= 1
    return min_size, simpleSplit(text, font, min_size, width)


def _centered(c: canvas.Canvas, y: float, lines: list[str], font: str, size: float, color) -> float:
    c.setFont(font, size)
    c.setFillColor(color)
    for line in lines:
        c.drawCentredString(PAGE_W / 2, y, line)
        y -= size * 1.3
    return y


def render_certificate(data: CertificateData) -> bytes:
    """Return the certificate as PDF bytes. Raises on any rendering problem."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(PAGE_W, PAGE_H))
    c.setTitle(f"{data.title} - {data.recipient_name}")
    c.setAuthor(data.issued_by)
    c.setSubject(data.event_name)

    # Double border
    c.setStrokeColor(NAVY)
    c.setLineWidth(4)
    c.rect(30, 30, PAGE_W - 60, PAGE_H - 60)
    c.setStrokeColor(GOLD)
    c.setLineWidth(1.5)
    c.rect(42, 42, PAGE_W - 84, PAGE_H - 84)

    y = PAGE_H - 125
    size, lines = _fit(data.title.upper(), "Helvetica-Bold", 34, 20, CONTENT_W)
    y = _centered(c, y, lines, "Helvetica-Bold", size, NAVY)

    c.setStrokeColor(GOLD)
    c.setLineWidth(1)
    c.line(PAGE_W / 2 - 90, y + 6, PAGE_W / 2 + 90, y + 6)

    y = _centered(c, y - 28, ["This certificate is proudly presented to"], "Helvetica", 14, GREY)

    size, lines = _fit(data.recipient_name, "Times-BoldItalic", 42, 18, CONTENT_W)
    y = _centered(c, y - 24, lines, "Times-BoldItalic", size, NAVY)

    y = _centered(c, y - 6, ["for successfully completing"], "Helvetica", 14, GREY)

    size, lines = _fit(data.event_name, "Helvetica-Bold", 24, 14, CONTENT_W)
    y = _centered(c, y - 16, lines, "Helvetica-Bold", size, NAVY)

    if data.achievement:
        _, lines = _fit(data.achievement, "Helvetica-Oblique", 15, 11, CONTENT_W)
        y = _centered(c, y - 6, lines, "Helvetica-Oblique", 15, GOLD)

    # Footer: date on the left, issuer + signature line on the right
    footer_y = 105
    c.setFillColor(GREY)
    c.setFont("Helvetica", 12)
    c.drawString(100, footer_y, f"Date: {data.issue_date.strftime('%d %B %Y')}")

    c.setStrokeColor(GREY)
    c.setLineWidth(0.8)
    c.line(PAGE_W - 330, footer_y + 18, PAGE_W - 100, footer_y + 18)
    c.drawRightString(PAGE_W - 100, footer_y, data.issued_by)

    c.setFont("Helvetica", 7.5)
    c.drawCentredString(PAGE_W / 2, 58, f"Certificate ID: {data.certificate_id}")

    c.showPage()
    c.save()
    return buf.getvalue()
