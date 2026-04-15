"""Deterministic entity-page creation, keyed by email address.

The old compile flow let the LLM invent entity slugs from display names.
That produced three concrete failures we had to chase:

- Duplicates by minor variation (`arjun-gaur`, `arjun-gaur-clean`,
  `arjun-gaur-v2` — three pages, one person).
- Garbage slugs when names don't slugify cleanly (`vishakha-indiamart`
  from `vishakha.01@indiamart.com` because the agent couldn't figure
  out the last name).
- Numeric drift (`akash-singh6.md` because the email was
  `akash.singh6@indiamart.com` but the 6 is not a name component).

Identity is the email. Everything else is presentation. This module
canonicalizes slugs from email addresses and handles page creation.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import psycopg
import structlog
import yaml

from src.config import settings
from src.utils import extract_frontmatter

logger = structlog.get_logger(__name__)

_EMAIL_RE = re.compile(r"^[a-z0-9._+\-]+@[a-z0-9.\-]+\.[a-z]+$")
_NON_SLUG_RE = re.compile(r"[^a-z0-9]+")

# Domains treated as internal. Slugs are uniform across all domains (full
# email), but the stub's frontmatter gets `is_external: true` when the
# domain is NOT in this set, so the browsing UI can badge externals.
INTERNAL_DOMAINS: frozenset[str] = frozenset({"indiamart.com"})


def is_external_email(email: str) -> bool:
    """True if the email's domain is NOT one of INTERNAL_DOMAINS.

    Used to decorate stub frontmatter, NOT to change slug shape — the
    slug rule is uniform so we never have to re-migrate if IndiaMART
    adds a second internal domain tomorrow.
    """
    email = email.strip().lower()
    if "@" not in email:
        return True
    _, _, domain = email.partition("@")
    return domain not in INTERNAL_DOMAINS


def email_to_slug(email: str) -> str:
    """One-to-one map from email address to filesystem-safe slug.

    Rule: lowercase, split on @, replace every non-alphanumeric run with
    a single hyphen, join with a hyphen.

    Examples:
        amit@indiamart.com           → amit-indiamart-com
        akash.singh6@indiamart.com   → akash-singh6-indiamart-com
        vishakha.01@indiamart.com    → vishakha-01-indiamart-com
        first.last+tag@gmail.com     → first-last-tag-gmail-com
    """
    if not isinstance(email, str):
        raise TypeError(f"email must be str, got {type(email).__name__}")
    email = email.strip().lower()
    if "@" not in email:
        raise ValueError(f"not an email: {email!r}")
    local, _, domain = email.partition("@")
    local_slug = _NON_SLUG_RE.sub("-", local).strip("-")
    domain_slug = _NON_SLUG_RE.sub("-", domain).strip("-")
    if not local_slug or not domain_slug:
        raise ValueError(f"email slug would be empty: {email!r}")
    return f"{local_slug}-{domain_slug}"


def is_valid_email(email: str) -> bool:
    """Loose RFC check — rejects obvious garbage but isn't exhaustive.

    Good enough for mailing-list ingest where emails already came from
    RFC-2822 headers and passed parseaddr.
    """
    return bool(_EMAIL_RE.match(email.strip().lower()))


def find_entity_by_email(email: str, entities_dir: Path | None = None) -> Path | None:
    """Return the existing entity page whose frontmatter `email` matches.

    This is the compatibility shim for legacy display-name slugs
    (`amit-agarwal.md` with `email: amit@indiamart.com`). New pages use
    `email_to_slug` directly; but until we migrate the whole wiki, the
    agent must be able to reach existing pages too.

    Returns the Path if found, else None.
    """
    email_lc = email.strip().lower()
    if entities_dir is None:
        entities_dir = settings.wiki_dir / "entities"
    if not entities_dir.exists():
        return None
    for md in entities_dir.glob("*.md"):
        try:
            fm = extract_frontmatter(md.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
        fm_email = fm.get("email")
        if isinstance(fm_email, str) and fm_email.strip().lower() == email_lc:
            return md
    return None


def _stub_markdown(email: str, display_name: str | None) -> str:
    """Minimal valid entity page. The agent enriches it on later mentions."""
    title = display_name.strip() if display_name and display_name.strip() else email
    frontmatter: dict[str, Any] = {
        "title": title,
        "page_type": "entity",
        "status": "current",
        "email": email,
        "is_external": is_external_email(email),
        "sources": [],
        "related": [],
    }
    yaml_block = yaml.safe_dump(
        frontmatter, sort_keys=False, allow_unicode=True, width=120
    ).rstrip()
    body = f"Email: {email}\n"
    return f"---\n{yaml_block}\n---\n\n{body}"


def _classify_evidence(counts: dict[str, int]) -> str:
    """Bucket per-role appearance counts into strong/medium/weak.

    Rules (mirrored in the `create_entity` tool docstring + prompt):

    - **Strong**: the email appears at least once as From or To — a direct
      author or addressee.
    - **Medium**: not in From/To anywhere, but shows up across ≥ 2 distinct
      threads (so they're a recurring CC or referenced participant, not a
      one-off tangential mention).
    - **Weak**: anything else (zero appearances, or CC-only on a single
      thread). Creating a stub from weak evidence is what filled wiki/
      with 1-line CC stubs.
    """
    if counts["from_count"] > 0 or counts["to_count"] > 0:
        return "strong"
    if counts["distinct_threads"] >= 2 and (counts["cc_count"] + counts["to_count"]) > 0:
        return "medium"
    return "weak"


_ZERO_COUNTS = {"from_count": 0, "to_count": 0, "cc_count": 0, "distinct_threads": 0}


def _evidence_counts(email: str) -> dict[str, int]:
    """Best-effort participants query; returns all-zeros if Postgres is down.

    A missing DB would otherwise turn every create_entity call into a hard
    failure — the compile pipeline treats the DB as optional infra for most
    operations, so we degrade to "weak" (no appearances found) rather than
    crashing. Import is deferred so tests that never touch participants can
    still run without a Postgres schema.

    Only `psycopg.Error` and connection-level OS errors are swallowed —
    application bugs (KeyError, AttributeError) propagate so they don't get
    silently masked as `weak_evidence` and quietly drop people from coverage.
    """
    try:
        from src.db.participants import count_appearances_by_role
    except ImportError:
        return dict(_ZERO_COUNTS)
    try:
        return count_appearances_by_role(email)
    except (psycopg.Error, OSError) as exc:
        logger.warning("evidence_counts DB unavailable", email=email, error=str(exc))
        return dict(_ZERO_COUNTS)


def create_entity_page(
    email: str,
    display_name: str | None = None,
    entities_dir: Path | None = None,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """Idempotently resolve an entity page by email.

    Existing-page lookup always wins — a page the agent created previously
    (or a legacy display-name slug with matching `email:` frontmatter) is
    returned unchanged regardless of current evidence strength. The gate
    only fires when we would otherwise write a NEW stub.

    For new pages, we consult `message_participants` via
    `count_appearances_by_role` and bucket the email into strong / medium /
    weak. Weak evidence (0-1 mentions, or CC-only on one thread) aborts
    creation unless `force=True`. Strong (From/To anywhere) or medium (≥ 2
    distinct threads with CC/to) pass through.

    Returns a dict ready for an LLM tool response. On success:
        {"ok": True, "slug": "amit-indiamart-com",
         "path": "wiki/entities/amit-indiamart-com.md",
         "created": True|False, "email": "amit@indiamart.com",
         "evidence_level": "strong"|"medium"|"forced"}
    On weak-evidence refusal (force=False):
        {"ok": False, "reason": "weak_evidence", "email": email_lc,
         "would_be_slug": slug, "evidence_summary": {...}, "guidance": "..."}
    On bad input:
        {"ok": False, "error": "invalid email: 'notanemail'"}
    """
    if not isinstance(email, str) or not email.strip():
        return {"ok": False, "error": "email is required"}
    email_lc = email.strip().lower()
    if not is_valid_email(email_lc):
        return {"ok": False, "error": f"invalid email: {email!r}"}

    if entities_dir is None:
        entities_dir = settings.wiki_dir / "entities"
    entities_dir.mkdir(parents=True, exist_ok=True)

    existing = find_entity_by_email(email_lc, entities_dir=entities_dir)
    if existing is not None:
        return {
            "ok": True,
            "slug": existing.stem,
            "path": str(existing),
            "created": False,
            "email": email_lc,
        }

    slug = email_to_slug(email_lc)
    path = entities_dir / f"{slug}.md"
    if path.exists():
        return {
            "ok": True,
            "slug": slug,
            "path": str(path),
            "created": False,
            "email": email_lc,
        }

    # Evidence gate — only applies to brand-new pages. Existing pages
    # (resolved above) bypass this check so we don't destabilise the wiki
    # on recompile.
    counts = _evidence_counts(email_lc)
    level = _classify_evidence(counts)
    if level == "weak" and not force:
        return {
            "ok": False,
            "reason": "weak_evidence",
            "email": email_lc,
            "would_be_slug": slug,
            "evidence_summary": {
                "from_count": counts["from_count"],
                "to_count": counts["to_count"],
                "cc_count": counts["cc_count"],
                "distinct_threads": counts["distinct_threads"],
            },
            "guidance": (
                "This entity has only CC-only or weak appearances. "
                "Pass force=True only if you are writing substantive content "
                "for them in this turn."
            ),
        }

    if force and level == "weak":
        # Surface every forced override in app logs so production audits can
        # spot agents (or future humans) over-using the escape hatch without
        # needing Langfuse traces to be available.
        logger.info(
            "create_entity_forced",
            email=email_lc,
            slug=slug,
            evidence_summary=counts,
        )

    path.write_text(_stub_markdown(email_lc, display_name), encoding="utf-8")
    return {
        "ok": True,
        "slug": slug,
        "path": str(path),
        "created": True,
        "email": email_lc,
        "evidence_level": "forced" if (force and level == "weak") else level,
    }
