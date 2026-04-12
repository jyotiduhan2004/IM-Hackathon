"""Attachment and inline image handler for email ingestion."""

from __future__ import annotations

import base64
from pathlib import Path

import structlog

from src.ingest.gmail import AttachmentRef
from src.ingest.gmail import GmailClient
from src.ingest.parser import ParsedEmail

logger = structlog.get_logger(__name__)


def save_attachments(
    gmail_client: GmailClient,
    parsed: ParsedEmail,
    raw_dir: Path,
    skip_download: bool = False,
) -> list[str]:
    """Download and save all attachments for a message.

    Args:
        gmail_client: Authenticated Gmail client
        parsed: ParsedEmail with attachment metadata
        raw_dir: Root raw/ directory (attachments saved to raw/attachments/{msg_id_short}/)
        skip_download: If True, don't actually download (for dry-run or reingest)

    Returns:
        List of relative paths (as strings) to saved attachments.
    """
    if not parsed.attachments:
        return []

    if skip_download:
        return [
            f"raw/attachments/{parsed.msg_id_short}/{att.filename}" for att in parsed.attachments
        ]

    attachments_root = raw_dir / "attachments" / parsed.msg_id_short
    attachments_root.mkdir(parents=True, exist_ok=True)
    saved_paths: list[str] = []

    for att in parsed.attachments:
        dest = attachments_root / _safe_filename(att.filename)

        if dest.exists():
            logger.debug("attachment already saved", path=str(dest))
            saved_paths.append(str(dest.relative_to(raw_dir.parent)))
            continue

        try:
            data = gmail_client.get_attachment(parsed.message_id, att.attachment_id)
            dest.write_bytes(data)
            rel_path = str(dest.relative_to(raw_dir.parent))
            saved_paths.append(rel_path)
            logger.info(
                "saved attachment",
                path=rel_path,
                size=len(data),
                mime=att.mime_type,
            )
        except Exception as e:  # noqa: BLE001 - log any download failure and continue
            logger.warning(
                "attachment download failed",
                filename=att.filename,
                error=str(e),
            )

    return saved_paths


def _safe_filename(name: str) -> str:
    """Sanitize a filename for safe filesystem storage."""
    import re

    safe = re.sub(r"[^\w\s.-]", "_", name)
    return safe.strip() or "unnamed"


async def caption_image(image_path: Path, model: str = "gpt-4o") -> str | None:
    """Generate a text caption for an image using a vision model.

    Uses LiteLLM for model-agnostic vision calls. Returns None on failure.
    """
    try:
        import litellm
    except ImportError:
        logger.warning("litellm not installed, skipping image captioning")
        return None

    try:
        # Image files are small (< 10MB typically); sync read is fine here even
        # from an async context. Wrapping in asyncio.to_thread would add more
        # noise than it's worth.
        data = image_path.read_bytes()  # noqa: ASYNC240
        mime = _detect_image_mime(image_path)
        b64 = base64.b64encode(data).decode("utf-8")
        data_url = f"data:{mime};base64,{b64}"

        response = await litellm.acompletion(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "Describe this image concisely in 1-2 sentences. "
                                "Focus on data, charts, diagrams, or text content. "
                                "If it contains a chart or table, extract the key data points. "
                                "If it's a screenshot, describe what's shown."
                            ),
                        },
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
            max_tokens=200,
        )
        caption = response.choices[0].message.content.strip()
        logger.info("captioned image", path=str(image_path), caption=caption[:100])
        return caption
    except Exception as e:  # noqa: BLE001
        logger.warning("image captioning failed", path=str(image_path), error=str(e))
        return None


def _detect_image_mime(path: Path) -> str:
    suffix = path.suffix.lower().lstrip(".")
    return {
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "gif": "image/gif",
        "webp": "image/webp",
        "bmp": "image/bmp",
    }.get(suffix, "application/octet-stream")


def is_image(att: AttachmentRef) -> bool:
    return att.is_image
