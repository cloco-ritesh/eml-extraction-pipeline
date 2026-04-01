"""
EML file parser: extracts headers, plain text, HTML body, and attachments
from a .eml file into a structured ParsedEmail dataclass.

Handles forwarded emails by stripping the forwarding wrapper and extracting
the original sender, date, subject, and body content.

Forwarding-strip logic adapted from the proven implementation in:
  real_estate_poc/ingestion/parsers/common.py
which handles Apple Mail, Gmail, Outlook, bold-markdown, and double-forwarded emails.
"""

import email as email_lib
import re
from dataclasses import dataclass
from datetime import datetime
from email.message import Message
from pathlib import Path


@dataclass(frozen=True)
class Attachment:
    """A single email attachment."""
    filename: str
    content_type: str
    data: bytes


@dataclass(frozen=True)
class ParsedEmail:
    """Structured representation of a parsed .eml file.

    For forwarded emails, the fields reflect the *original* email
    (not the forwarder), and the body is stripped of forwarding noise.
    """
    subject: str
    sender: str
    recipients: list[str]
    date: str
    plain_text: str
    html_body: str
    attachments: list[Attachment]
    raw_headers: dict[str, str]
    is_forwarded: bool


# ---------------------------------------------------------------------------
# MIME helpers (same as real_estate_poc/ingestion/parsers/common.py)
# ---------------------------------------------------------------------------

def _get_plain_text(msg: Message) -> str:
    """Extract and decode the text/plain part from an email message."""
    for part in msg.walk():
        if part.get_content_type() == "text/plain":
            payload = part.get_payload(decode=True)
            if payload:
                charset = part.get_content_charset() or "utf-8"
                return payload.decode(charset, errors="replace")
    return ""


def _get_html(msg: Message) -> str:
    """Extract and decode the text/html part from an email message."""
    for part in msg.walk():
        if part.get_content_type() == "text/html":
            payload = part.get_payload(decode=True)
            if payload:
                charset = part.get_content_charset() or "utf-8"
                return payload.decode(charset, errors="replace")
    return ""


def _extract_attachments(msg: Message) -> list[Attachment]:
    """Extract non-inline attachments from the email."""
    attachments: list[Attachment] = []
    for part in msg.walk():
        content_disposition = str(part.get("Content-Disposition", ""))
        if "attachment" not in content_disposition:
            continue
        filename = part.get_filename() or "unnamed"
        content_type = part.get_content_type()
        data = part.get_payload(decode=True)
        if data:
            attachments.append(
                Attachment(filename=filename, content_type=content_type, data=data)
            )
    return attachments


def _get_recipients(msg: Message) -> list[str]:
    """Extract all recipient addresses from To, Cc, Bcc headers."""
    recipients: list[str] = []
    for header in ("To", "Cc", "Bcc"):
        value = msg.get(header, "")
        if value:
            recipients.extend(addr.strip() for addr in value.split(",") if addr.strip())
    return recipients


def _get_headers(msg: Message) -> dict[str, str]:
    """Extract all headers as a flat dict (last value wins for duplicates)."""
    return {key: value for key, value in msg.items()}


# ---------------------------------------------------------------------------
# Forwarding detection
# ---------------------------------------------------------------------------

def _is_forwarded(subject: str, plain_text: str) -> bool:
    """Detect if this email is a forward."""
    if re.match(r"^(Fwd?|FW):\s", subject, re.IGNORECASE):
        return True
    if "Begin forwarded message" in plain_text[:500]:
        return True
    if "---------- Forwarded message ----------" in plain_text[:500]:
        return True
    return False


def _strip_subject_fwd(subject: str) -> str:
    """Remove all Fwd:/FW: prefixes from a subject line."""
    return re.sub(r"^(Fwd?:\s*|FW:\s*)+", "", subject, flags=re.IGNORECASE).strip()


# ---------------------------------------------------------------------------
# Original-email extraction from forwarded headers
# Adapted from real_estate_poc/ingestion/parsers/common.py:extract_report_date
# Handles: Outlook *Sent:*, Apple Mail >> Date:, Gmail/bold-markdown *Date: *,
#          double-forwarded (innermost From: block date preferred)
# ---------------------------------------------------------------------------

def _extract_original_date(raw_plain: str) -> str | None:
    """Extract the original email date from forwarded headers in the raw body.

    Uses the raw (un-stripped) plain text so >> prefixes are still present.
    Handles three forwarding formats plus double-forwarded emails.
    """
    # Outlook: '*Sent:* Monday, March 16, 2026 6:52:18 AM'
    sent_match = re.search(r"\*Sent:\*\s*\w+,\s*(\w+ \d{1,2}, \d{4})", raw_plain)
    if sent_match:
        try:
            dt = datetime.strptime(sent_match.group(1), "%B %d, %Y")
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            pass

    # Prefer the date nearest the original sender's From: block.
    # Handles both Subject-before-Date and Date-before-Subject header orders.
    from_block = re.search(
        r"From:.*?\n"
        r"(?:"
        r"\*?(?:>>)?\s*Subject:.*?\n\*?(?:>>)?\s*Date: \*?\s*(\d{1,2} \w+ \d{4})"
        r"|"
        r"\*?(?:>>)?\s*Date: \*?\s*(\d{1,2} \w+ \d{4}).*?\n\*?(?:>>)?\s*Subject:"
        r")",
        raw_plain,
        re.DOTALL,
    )
    if from_block:
        date_str = (from_block.group(1) or from_block.group(2) or "").strip()
        if date_str:
            try:
                dt = datetime.strptime(date_str, "%d %B %Y")
                return dt.strftime("%Y-%m-%d")
            except ValueError:
                pass

    # Apple Mail quoted: '>> Date: 16 March 2026 at ...'
    apple_match = re.search(r">> Date:\s*(\d{1,2} \w+ \d{4})", raw_plain)
    if apple_match:
        try:
            dt = datetime.strptime(apple_match.group(1).strip(), "%d %B %Y")
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            pass

    # Bold-markdown: '*Date: *27 March 2026 at ...'
    bold_match = re.search(r"\*Date: \*\s*(\d{1,2} \w+ \d{4})", raw_plain)
    if bold_match:
        try:
            dt = datetime.strptime(bold_match.group(1).strip(), "%d %B %Y")
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            pass

    return None


def _extract_original_sender(raw_plain: str) -> str | None:
    """Extract the original sender from forwarded headers in the raw body."""
    # Apple Mail: '>> From: Name <email>'
    # Gmail:      'From: Name <email>'
    # Outlook:    '*From:* Name'
    # Bold-md:    '*From: *Name <email>'
    for pattern in [
        r"(?:>>?\s*)From:\s*(.+)",
        r"\*From:\*\s*(.+)",
        r"\*From: \*\s*(.+)",
    ]:
        match = re.search(pattern, raw_plain)
        if match:
            sender = match.group(1).strip().rstrip("*")
            if sender:
                return sender
    return None


def _extract_original_subject(raw_plain: str) -> str | None:
    """Extract the original subject from forwarded headers in the raw body."""
    for pattern in [
        r"(?:>>?\s*)Subject:\s*(.+)",
        r"\*Subject:\*\s*(.+)",
        r"\*Subject: \*\s*(.+)",
    ]:
        match = re.search(pattern, raw_plain)
        if match:
            subject = match.group(1).strip().rstrip("*")
            if subject:
                return subject
    return None


# ---------------------------------------------------------------------------
# Body stripping
# Adapted from real_estate_poc/ingestion/parsers/common.py:extract_proping_body
# Generalized: no Proping-specific start/end markers — works with any forwarded email.
# ---------------------------------------------------------------------------

def _strip_forwarding_wrapper(plain_text: str) -> str:
    """Strip the forwarding envelope and return the original email body.

    Removes:
    - "Begin forwarded message:" / "---------- Forwarded message ----------" preamble
    - Forwarded header block (From/Date/Subject/To)
    - Apple Mail ">>" quote prefixes on every line
    - Outlook *bold* header blocks
    - "You don't often get email from..." / "Learn why" noise
    - Bare tracking URLs
    """
    text = plain_text

    # 1. Cut everything before the forwarding marker
    begin_fwd = re.search(
        r"(?:Begin forwarded message|-{5,}\s*Forwarded message\s*-{5,}):\s*\n?",
        text,
        re.IGNORECASE,
    )
    if begin_fwd:
        text = text[begin_fwd.end():]

    # 2. Normalize line endings
    text = text.replace("\r\n", "\n")

    # 3. Strip Apple Mail / Outlook forwarding quote prefix (">> ") from every line.
    #    Apple Mail uses ">> " (sometimes ">>  " for indented content).
    #    (From real_estate_poc/ingestion/parsers/common.py)
    if re.search(r"^>> ", text, re.MULTILINE):
        text = re.sub(r"^>>  ?", "", text, flags=re.MULTILINE)

    # 4. Remove the forwarded header block (From/Subject/Date/To lines at top)
    header_block = re.match(
        r"\s*(?:(?:\*?)(?:From|Subject|Date|To|Reply-To|Cc)[:*]\s*.+\n)+",
        text,
    )
    if header_block:
        text = text[header_block.end():]

    # 5. Strip noise lines
    text = re.sub(r"You don't often get email from.+?(?:\n|$)", "", text)
    text = re.sub(r"Learn why this is important\s*<[^>]*>\s*", "", text)

    # 6. Strip bare tracking URLs (long encoded URLs on their own line)
    text = re.sub(
        r"^\s*<https?://[^\s>]{50,}>\s*$",
        "",
        text,
        flags=re.MULTILINE,
    )

    # 7. Collapse excessive blank lines
    text = re.sub(r"\n{4,}", "\n\n\n", text)

    return text.strip()


def _strip_html_forwarding(html: str) -> str:
    """Strip forwarding wrapper from HTML content.

    Removes the outer forwarding envelope (blockquote wrappers, Gmail quote divs).
    """
    if not html:
        return html

    # Apple Mail wraps in <blockquote type="cite">
    bq_match = re.search(
        r'<blockquote[^>]*type=["\']cite["\'][^>]*>(.*)</blockquote>',
        html,
        re.DOTALL | re.IGNORECASE,
    )
    if bq_match:
        return bq_match.group(1).strip()

    # Gmail wraps in <div class="gmail_quote">
    gq_match = re.search(
        r'<div\s+class=["\']gmail_quote["\'][^>]*>(.*)</div>',
        html,
        re.DOTALL | re.IGNORECASE,
    )
    if gq_match:
        return gq_match.group(1).strip()

    return html


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_eml(eml_path: Path) -> ParsedEmail:
    """Parse a .eml file into a structured ParsedEmail object.

    For forwarded emails, extracts the original sender, date, subject, and
    body — stripping all forwarding noise so downstream extractors see clean content.
    """
    with open(eml_path, "rb") as f:
        msg = email_lib.message_from_bytes(f.read())

    raw_subject = msg.get("Subject", "")
    raw_plain = _get_plain_text(msg)
    raw_html = _get_html(msg)
    forwarded = _is_forwarded(raw_subject, raw_plain)

    if forwarded:
        # Extract original email metadata from forwarded headers (using raw text)
        subject = _extract_original_subject(raw_plain) or _strip_subject_fwd(raw_subject)
        sender = _extract_original_sender(raw_plain) or msg.get("From", "")
        date = _extract_original_date(raw_plain) or msg.get("Date", "")
        plain_text = _strip_forwarding_wrapper(raw_plain)
        html_body = _strip_html_forwarding(raw_html)
    else:
        subject = raw_subject
        sender = msg.get("From", "")
        date = msg.get("Date", "")
        plain_text = raw_plain
        html_body = raw_html

    return ParsedEmail(
        subject=subject,
        sender=sender,
        recipients=_get_recipients(msg),
        date=date,
        plain_text=plain_text,
        html_body=html_body,
        attachments=_extract_attachments(msg),
        raw_headers=_get_headers(msg),
        is_forwarded=forwarded,
    )
