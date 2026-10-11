"""Stamps "CERTIFIED TRUE COPY" in the lower-right corner of a served file.

The stored original is never modified — the stamp is applied per request.
Downloads are converted to PDF so the stamped copy is what leaves the registry.
"""
import io
import logging
import os
import shutil
import subprocess
import tempfile

from PIL import Image
from pypdf import PdfReader, PdfWriter
from reportlab.lib.colors import Color
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

STAMP_OFFICE = "MAYOR'S OFFICE"
STAMP_TITLE = "CERTIFIED TRUE COPY"
WATERMARK_TEXT = STAMP_TITLE
PDF_EXTENSIONS = {".pdf"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}
# Word files need an office suite to become a faithful PDF; see office_converter().
OFFICE_EXTENSIONS = {".doc", ".docx"}

logger = logging.getLogger(__name__)

# Where LibreOffice usually lands. DOC_TO_PDF_COMMAND in .env overrides this.
_LIBREOFFICE_PATHS = [
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    "/usr/bin/soffice",
    "/usr/bin/libreoffice",
    "/opt/libreoffice/program/soffice",
]

# Violet, matching the office's rubber stamp ink.
STAMP_INK = Color(0.30, 0.24, 0.63, alpha=0.9)
MARGIN = 26


def office_converter():
    """The LibreOffice binary to turn Word files into PDF, or None if there is none.

    Word documents are the one upload we cannot render ourselves, and a reflowed
    approximation must never be stamped "certified true copy" — so without a
    converter those files stay uncertified rather than becoming a false copy.
    """
    from django.conf import settings

    configured = getattr(settings, "DOC_TO_PDF_COMMAND", "")
    if configured and os.path.exists(configured):
        return configured
    found = shutil.which("soffice") or shutil.which("libreoffice")
    if found:
        return found
    for path in _LIBREOFFICE_PATHS:
        if os.path.exists(path):
            return path
    return None


def can_certify(filename):
    """True when a download of this file can leave as a stamped PDF."""
    ext = os.path.splitext(filename)[1].lower()
    if ext in PDF_EXTENSIONS or ext in IMAGE_EXTENSIONS:
        return True
    if ext in OFFICE_EXTENSIONS:
        return office_converter() is not None
    return False


def certified_pdf(file_obj, filename, subtitle=""):
    """Return a stamped PDF for any supported file, or None if unsupported."""
    ext = os.path.splitext(filename)[1].lower()
    if ext in IMAGE_EXTENSIONS:
        file_obj = _image_to_pdf(file_obj)
    elif ext in OFFICE_EXTENSIONS:
        file_obj = _office_to_pdf(file_obj, ext)
        if file_obj is None:
            return None
    elif ext not in PDF_EXTENSIONS:
        return None
    return _stamp_pdf(file_obj, subtitle)


def _office_to_pdf(file_obj, ext):
    """Convert a Word file with LibreOffice, keeping its layout intact."""
    converter = office_converter()
    if not converter:
        return None
    with tempfile.TemporaryDirectory() as work:
        source = os.path.join(work, f"source{ext}")
        with open(source, "wb") as handle:
            handle.write(file_obj.read())
        try:
            subprocess.run(
                [converter, "--headless", "--norestore", "--convert-to", "pdf",
                 "--outdir", work, source],
                check=True, capture_output=True, timeout=120,
            )
        except (subprocess.SubprocessError, OSError):
            logger.exception("LibreOffice could not convert the document to PDF")
            return None
        produced = os.path.join(work, "source.pdf")
        if not os.path.exists(produced):
            logger.error("LibreOffice produced no PDF for the document")
            return None
        with open(produced, "rb") as handle:
            return io.BytesIO(handle.read())


def _draw_stamp(pdf, width, height, subtitle):
    """The office rubber stamp: boxed, with blank BY and DATE lines to sign by hand."""
    size = max(min(width, height) / 62, 7)
    pad = size * 1.15
    line_gap = size * 1.75
    label_font, body_font = "Helvetica-Bold", "Helvetica-Bold"

    title_w = pdf.stringWidth(STAMP_TITLE, body_font, size)
    rule_w = max(title_w * 0.62, size * 7)
    date_label_w = pdf.stringWidth("DATE: ", label_font, size * 0.92)
    box_w = max(title_w, date_label_w + rule_w) + pad * 2
    box_h = pad * 2 + line_gap * 3.6

    x = max(width - MARGIN - box_w, MARGIN)
    y = MARGIN

    pdf.saveState()
    pdf.setFillColor(Color(1, 1, 1, alpha=0.78))
    pdf.setStrokeColor(STAMP_INK)
    pdf.setLineWidth(max(size * 0.16, 1.1))
    pdf.rect(x, y, box_w, box_h, stroke=1, fill=1)

    pdf.setFillColor(STAMP_INK)
    centre = x + box_w / 2
    top = y + box_h - pad

    pdf.setFont(body_font, size * 0.92)
    pdf.drawCentredString(centre, top - size * 0.9, STAMP_OFFICE)
    pdf.setFont(body_font, size)
    pdf.drawCentredString(centre, top - size * 0.9 - line_gap, STAMP_TITLE)

    # BY: and DATE: are left blank, as on the physical stamp
    pdf.setLineWidth(max(size * 0.09, 0.7))
    for i, label in enumerate(("BY:", "DATE:")):
        baseline = top - size * 0.9 - line_gap * (2.1 + i)
        pdf.setFont(label_font, size * 0.92)
        pdf.drawString(x + pad, baseline, label)
        rule_x = x + pad + date_label_w
        pdf.line(rule_x, baseline - size * 0.12, x + box_w - pad, baseline - size * 0.12)

    if subtitle:
        pdf.setFont("Helvetica", size * 0.62)
        pdf.drawRightString(x + box_w, y - size * 0.75, subtitle)
    pdf.restoreState()


def _overlay(width, height, subtitle):
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(width, height))
    _draw_stamp(pdf, width, height, subtitle)
    pdf.save()
    buffer.seek(0)
    return PdfReader(buffer).pages[0]


def _stamp_pdf(file_obj, subtitle):
    reader = PdfReader(file_obj)
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception:
            return None
    writer = PdfWriter()
    for page in reader.pages:
        box = page.mediabox
        page.merge_page(_overlay(float(box.width), float(box.height), subtitle))
        writer.add_page(page)
    out = io.BytesIO()
    writer.write(out)
    out.seek(0)
    return out


def _image_to_pdf(file_obj):
    """Scan on one A4 page, scaled to fit with a small margin."""
    image = Image.open(file_obj)
    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")

    page_w, page_h = A4
    usable_w, usable_h = page_w - MARGIN * 2, page_h - MARGIN * 2
    scale = min(usable_w / image.width, usable_h / image.height)
    draw_w, draw_h = image.width * scale, image.height * scale

    out = io.BytesIO()
    pdf = canvas.Canvas(out, pagesize=A4)
    pdf.drawImage(
        ImageReader(image),
        (page_w - draw_w) / 2, (page_h - draw_h) / 2,
        width=draw_w, height=draw_h,
    )
    pdf.save()
    out.seek(0)
    return out
