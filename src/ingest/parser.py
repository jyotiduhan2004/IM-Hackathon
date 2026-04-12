"""Convert Gmail API messages into raw/ markdown files with YAML frontmatter."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import datetime
from pathlib import Path

import structlog
import yaml

from src.ingest.gmail import AttachmentRef
from src.ingest.gmail import RawMessage

logger = structlog.get_logger(__name__)


@dataclass
class ParsedEmail:
    """Parsed email ready for raw/ markdown serialization."""

    message_id: str
    thread_id: str
    subject: str
    from_: str
    to: list[str]
    cc: list[str]
    date: datetime
    in_reply_to: str | None
    labels: list[str]
    body: str
    attachments: list[AttachmentRef]
    inline_images: list[dict[str, str]] = field(default_factory=list)

    @property
    def msg_id_short(self) -> str:
        """8-char hash of Message-ID for filenames."""
        return hashlib.sha256(self.message_id.encode()).hexdigest()[:8]


def slugify(text: str, max_length: int = 50) -> str:
    """Convert text to URL-safe lowercase slug."""
    if not text:
        return "no-subject"
    # Strip common Re:/Fwd: prefixes
    text = re.sub(r"^(re|fwd|fw):\s*", "", text, flags=re.IGNORECASE)
    # Remove non-alphanumeric except spaces and hyphens
    text = re.sub(r"[^\w\s-]", "", text.lower())
    # Collapse whitespace to hyphens
    text = re.sub(r"[\s_]+", "-", text).strip("-")
    return text[:max_length] or "no-subject"


def _split_addresses(header_value: str) -> list[str]:
    """Split a To/Cc header into individual addresses."""
    if not header_value:
        return []
    # Simple split on comma, strips whitespace
    return [a.strip() for a in header_value.split(",") if a.strip()]


def parse_message(raw: RawMessage) -> ParsedEmail:
    """Parse a Gmail API message into structured ParsedEmail."""
    headers = raw.headers

    # Extract body: prefer plain text, fall back to HTML
    body = raw.body_plain.strip()
    if not body and raw.body_html:
        body = _html_to_markdown(raw.body_html)

    # Parse date from header if available, else use internalDate
    date_str = headers.get("Date", "")
    parsed_date = _parse_date(date_str) or raw.date

    return ParsedEmail(
        message_id=headers.get("Message-ID", raw.id),
        thread_id=raw.thread_id,
        subject=headers.get("Subject", "(no subject)"),
        from_=headers.get("From", ""),
        to=_split_addresses(headers.get("To", "")),
        cc=_split_addresses(headers.get("Cc", "")),
        date=parsed_date,
        in_reply_to=headers.get("In-Reply-To"),
        labels=raw.label_ids,
        body=body,
        attachments=raw.attachments,
    )


def _parse_date(date_str: str) -> datetime | None:
    """Best-effort parsing of RFC 2822 date headers."""
    if not date_str:
        return None
    try:
        from email.utils import parsedate_to_datetime

        return parsedate_to_datetime(date_str)
    except (TypeError, ValueError):
        return None


def _html_to_markdown(html: str) -> str:
    """Convert HTML body to clean markdown using markitdown.

    Falls back to stripping tags if markitdown is unavailable.
    """
    try:
        import io

        from markitdown import MarkItDown

        md = MarkItDown()
        result = md.convert_stream(
            io.BytesIO(html.encode("utf-8")),
            file_extension=".html",
        )
        return result.text_content.strip()
    except ImportError:
        return re.sub(r"<[^>]+>", "", html).strip()
    except Exception as e:  # noqa: BLE001 - fall back on any conversion error
        logger.warning("markitdown html conversion failed, falling back", error=str(e))
        return re.sub(r"<[^>]+>", "", html).strip()


def generate_filename(parsed: ParsedEmail) -> str:
    """Generate raw/ filename: YYYY-MM-DD_{subject-slug}_{msg-id-short}.md"""
    date_str = parsed.date.strftime("%Y-%m-%d")
    subject_slug = slugify(parsed.subject)
    return f"{date_str}_{subject_slug}_{parsed.msg_id_short}.md"


def to_raw_markdown(parsed: ParsedEmail, attachment_paths: list[str] | None = None) -> str:
    """Convert a ParsedEmail to the raw/ markdown format with YAML frontmatter."""
    frontmatter: dict[str, object] = {
        "message_id": parsed.message_id,
        "thread_id": parsed.thread_id,
        "subject": parsed.subject,
        "from": parsed.from_,
        "to": parsed.to,
        "cc": parsed.cc,
        "date": parsed.date.isoformat(),
        "in_reply_to": parsed.in_reply_to,
        "labels": parsed.labels,
        "has_attachments": bool(parsed.attachments),
        "attachment_files": attachment_paths or [],
        "inline_images": parsed.inline_images,
        "ingested_at": datetime.now(UTC).isoformat(),
        "compiled": False,
    }

    yaml_block = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True, width=120)

    parts = [
        "---",
        yaml_block.rstrip(),
        "---",
        "",
        parsed.body or "(no body content)",
    ]

    if attachment_paths:
        parts.append("")
        parts.append("---")
        parts.append("*Attachments:*")
        for path in attachment_paths:
            name = Path(path).name
            parts.append(f"- [{name}]({path})")

    return "\n".join(parts) + "\n"


def write_raw_email(
    parsed: ParsedEmail,
    raw_dir: Path,
    attachment_paths: list[str] | None = None,
) -> Path:
    """Write a parsed email to raw_dir as a markdown file. Returns the path."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    filename = generate_filename(parsed)
    path = raw_dir / filename

    if path.exists():
        logger.debug("raw file already exists, skipping", path=str(path))
        return path

    content = to_raw_markdown(parsed, attachment_paths=attachment_paths)
    path.write_text(content, encoding="utf-8")
    logger.info("wrote raw email", path=str(path), subject=parsed.subject[:60])
    return path
