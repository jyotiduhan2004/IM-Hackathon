"""Smoke test: parse a fake Gmail API message and write raw markdown."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.ingest.gmail import RawMessage  # noqa: E402
from src.ingest.parser import generate_filename  # noqa: E402
from src.ingest.parser import parse_message  # noqa: E402
from src.ingest.parser import to_raw_markdown  # noqa: E402
from src.ingest.parser import write_raw_email  # noqa: E402


def _make_fake_message() -> RawMessage:
    return RawMessage(
        id="19a1b2c3d4e5",
        thread_id="thread-001",
        label_ids=["INBOX", "IMPORTANT"],
        headers={
            "From": "Jane Doe <jane@company.com>",
            "To": "all-hands@company.com",
            "Cc": "finance@company.com, ops@company.com",
            "Subject": "Re: Updated Reimbursement Policy",
            "Date": "Fri, 05 Apr 2026 14:30:00 +0530",
            "Message-ID": "<CABx123abc@mail.gmail.com>",
            "In-Reply-To": "<CABx000previous@mail.gmail.com>",
        },
        body_plain=(
            "Hi team,\n\n"
            "Following the Q1 review, we're updating the reimbursement policy.\n\n"
            "Key changes:\n"
            "- Travel allowance: 8000/day (was 5000)\n"
            "- Meal limit: 1500/meal (was 1000)\n"
            "- Submission: 15 days (was 30)\n\n"
            "This supersedes the March 15 policy.\n\n"
            "Best,\nJane"
        ),
        body_html="",
        attachments=[],
        internal_date_ms=1743856200000,  # 2026-04-05
    )


def test_parse_message_smoke() -> None:
    raw = _make_fake_message()
    parsed = parse_message(raw)

    assert parsed.subject == "Re: Updated Reimbursement Policy"
    assert parsed.from_ == "Jane Doe <jane@company.com>"
    assert parsed.to == ["all-hands@company.com"]
    assert parsed.cc == ["finance@company.com", "ops@company.com"]
    assert parsed.message_id == "<CABx123abc@mail.gmail.com>"
    assert parsed.thread_id == "thread-001"
    assert parsed.in_reply_to == "<CABx000previous@mail.gmail.com>"
    assert len(parsed.body) > 0


def test_generate_filename_strips_re_prefix() -> None:
    raw = _make_fake_message()
    parsed = parse_message(raw)
    filename = generate_filename(parsed)

    assert filename.startswith("2026-04-")
    assert filename.endswith(".md")
    assert "re-" not in filename.lower() or "-review-" in filename.lower()
    # Should contain subject slug
    assert "updated-reimbursement-policy" in filename


def test_to_raw_markdown_produces_yaml_frontmatter() -> None:
    raw = _make_fake_message()
    parsed = parse_message(raw)
    markdown = to_raw_markdown(parsed)

    assert markdown.startswith("---\n")
    assert "compiled: false" in markdown
    assert "Updated Reimbursement Policy" in markdown
    assert "jane@company.com" in markdown
    # Body should be after the closing ---
    assert "Key changes:" in markdown


def test_write_raw_email_creates_file() -> None:
    raw = _make_fake_message()
    parsed = parse_message(raw)

    with tempfile.TemporaryDirectory() as tmp:
        raw_dir = Path(tmp)
        path = write_raw_email(parsed, raw_dir)

        assert path.exists()
        content = path.read_text()
        assert "compiled: false" in content
        assert parsed.subject in content


if __name__ == "__main__":
    test_parse_message_smoke()
    test_generate_filename_strips_re_prefix()
    test_to_raw_markdown_produces_yaml_frontmatter()
    test_write_raw_email_creates_file()
    print("All smoke tests passed.")
