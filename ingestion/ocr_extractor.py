"""
OCR-based extraction: converts email HTML body and attachments to images,
runs EasyOCR, and parses the text into the same ExtractedFields schema.
"""

import logging
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from ingestion.parser import ParsedEmail
from ingestion.schemas import (
    ExtractedFields,
    ExtractionResult,
    TokenUsage,
    Entity,
    KeyDate,
    MonetaryAmount,
)

logger = logging.getLogger(__name__)

_reader: Any = None  # easyocr.Reader, lazy-loaded


def _get_reader() -> Any:
    """Lazy-initialize the EasyOCR reader (downloads models on first use)."""
    global _reader
    if _reader is None:
        import easyocr
        _reader = easyocr.Reader(["en"], gpu=False)
    return _reader


WKHTMLTOPDF_IMAGE = "surnet/alpine-wkhtmltopdf:3.20.3-0.12.6-small"


def _html_to_image(html_content: str) -> bytes | None:
    """Render HTML to a PNG image using two Docker steps:
    1. wkhtmltopdf: HTML → PDF
    2. ImageMagick (alpine): PDF → PNG

    No local system binaries required — only Docker.
    Returns None if Docker is unavailable or conversion fails.
    """
    try:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            html_file = tmp_path / "input.html"
            pdf_file = tmp_path / "output.pdf"
            png_file = tmp_path / "output.png"
            html_file.write_text(html_content, encoding="utf-8")

            # Step 1: HTML → PDF via wkhtmltopdf
            pdf_result = subprocess.run(
                [
                    "docker", "run", "--rm",
                    "-v", f"{tmp_path}:/data",
                    WKHTMLTOPDF_IMAGE,
                    "--quiet",
                    "--log-level", "none",
                    "/data/input.html",
                    "/data/output.pdf",
                ],
                capture_output=True,
                timeout=30,
            )

            if pdf_result.returncode != 0 or not pdf_file.exists():
                logger.warning("wkhtmltopdf failed: %s", pdf_result.stderr.decode(errors="replace").strip())
                return None

            # Step 2: PDF → PNG via ImageMagick in Alpine
            png_result = subprocess.run(
                [
                    "docker", "run", "--rm",
                    "--entrypoint", "magick",
                    "-v", f"{tmp_path}:/data",
                    "dpokidov/imagemagick:latest",
                    "-density", "150",
                    "/data/output.pdf[0]",
                    "-colorspace", "sRGB",
                    "/data/output.png",
                ],
                capture_output=True,
                timeout=30,
            )

            if png_result.returncode != 0:
                logger.warning("ImageMagick convert failed: %s", png_result.stderr.decode(errors="replace").strip())
                return None

            if png_file.exists():
                return png_file.read_bytes()
            return None

    except FileNotFoundError:
        logger.warning("Docker not found — skipping HTML-to-image conversion")
        return None
    except subprocess.TimeoutExpired:
        logger.warning("Docker HTML-to-image timed out")
        return None
    except Exception as exc:
        logger.warning("HTML-to-image conversion failed: %s", exc)
        return None


def _ocr_image(image_data: bytes) -> list[tuple[str, float]]:
    """Run EasyOCR on image bytes. Returns list of (text, confidence) tuples."""
    reader = _get_reader()
    results = reader.readtext(image_data)
    return [(text, conf) for (_bbox, text, conf) in results]


def _ocr_plain_text(text: str) -> list[tuple[str, float]]:
    """Wrap plain text as pseudo-OCR output with confidence 1.0."""
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    return [(line, 1.0) for line in lines]


RE_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
RE_DATE = re.compile(r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s+\d{4}", re.IGNORECASE)
RE_MONEY = re.compile(r"\$\s*([\d,]+(?:\.\d{2})?)")


def _parse_ocr_to_fields(
    ocr_results: list[tuple[str, float]],
    parsed_email: ParsedEmail,
) -> ExtractedFields:
    """Parse OCR text output into ExtractedFields using regex heuristics."""
    full_text = "\n".join(text for text, _conf in ocr_results)

    # Sender info from email headers (more reliable than OCR)
    sender_email_match = RE_EMAIL.search(parsed_email.sender)
    sender_email = sender_email_match.group(0) if sender_email_match else None
    sender_name = parsed_email.sender.split("<")[0].strip().strip('"') or None

    # Dates from OCR text
    date_matches = RE_DATE.findall(full_text)
    key_dates = [KeyDate(label=f"date_{i}", date=d) for i, d in enumerate(date_matches)]

    # Monetary amounts from OCR text
    money_matches = RE_MONEY.findall(full_text)
    monetary_amounts = []
    for i, amount_str in enumerate(money_matches):
        cleaned = amount_str.replace(",", "")
        try:
            monetary_amounts.append(
                MonetaryAmount(label=f"amount_{i}", amount=float(cleaned))
            )
        except ValueError:
            continue

    # Emails found in body as entities
    entities: list[Entity] = []
    for email_addr in RE_EMAIL.findall(full_text):
        entities.append(Entity(name=email_addr, type="person", value=email_addr))

    return ExtractedFields(
        sender_name=sender_name,
        sender_email=sender_email,
        subject=parsed_email.subject,
        date=parsed_email.date,
        category="other",
        entities=entities,
        key_dates=key_dates,
        monetary_amounts=monetary_amounts,
        summary=f"OCR extracted {len(ocr_results)} text blocks from email",
    )


def extract_with_ocr(parsed_email: ParsedEmail) -> ExtractionResult:
    """Convert email content to images, OCR them, and parse into ExtractedFields."""
    start = time.monotonic()
    all_ocr: list[tuple[str, float]] = []

    # Try HTML-to-image OCR first
    if parsed_email.html_body:
        image_data = _html_to_image(parsed_email.html_body)
        if image_data:
            all_ocr.extend(_ocr_image(image_data))

    # OCR any image attachments
    for att in parsed_email.attachments:
        if att.content_type.startswith("image/"):
            all_ocr.extend(_ocr_image(att.data))

    # Fallback: treat plain text as OCR input
    if not all_ocr and parsed_email.plain_text:
        all_ocr = _ocr_plain_text(parsed_email.plain_text)

    extracted = _parse_ocr_to_fields(all_ocr, parsed_email)
    latency = time.monotonic() - start

    return ExtractionResult(
        model_name="ocr",
        extracted=extracted,
        token_usage=TokenUsage(),
        estimated_cost_usd=0.0,
        latency_seconds=round(latency, 3),
        raw_response="\n".join(f"[{conf:.2f}] {text}" for text, conf in all_ocr[:50]),
    )
