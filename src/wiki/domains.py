"""North-Star domain taxonomy + domain assignment for wiki pages.

Defines the 8 canonical domain hubs (see docs/NORTH-STAR.md) and the
rules for routing a topic/system page to its hub(s) — explicit
frontmatter first, then tags, then keyword inference, then slug-prefix
tiebreaker. Extracted from the legacy `src/compile/compiler.py` in Phase
1A.1; `src/compile/` was deleted in the 2026-04-29 refactor, so callers
import these names directly from `src.wiki.domains`.

Pure data + pure functions — no IO, no agent state. The wiki package's
landing rebuild and validators consume these symbols.
"""

from __future__ import annotations

from typing import Any

import structlog

from src.wiki.pages import _first_paragraph

logger = structlog.get_logger(__name__)


_DOMAINS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "buyer-experience",
        "Buyer Experience",
        ("buymer", "buylead", "buyer app", "search ux", "lens", "whatsapp buyer"),
    ),
    (
        "seller-experience",
        "Seller Experience",
        ("auditmate", "seller im", "seller dashboard", "specs", "compliance"),
    ),
    (
        "marketplace-discovery",
        "Marketplace & Discovery",
        (
            "mcat",
            "isq",
            "photosearch",
            "ranking",
            "categorization",
            "recommendations",
        ),
    ),
    (
        "platform-reliability",
        "Platform Reliability & Infrastructure",
        ("gke", "mesh pg", "db ops", "api framework", "performance"),
    ),
    (
        "trust-safety",
        "Trust, Safety & Compliance",
        ("kyc", "gst", "fraud", "moderation", "payment protection", "trustseal"),
    ),
    (
        "ai-automation",
        "AI Agents & Automation",
        ("crashagent", "whatsapp 9696", "autonomous assistant"),
    ),
    (
        "growth-monetization",
        "Growth, Monetization & Partnerships",
        ("export", "ads", "affiliates", "google merchant", "tenders"),
    ),
    (
        "engineering-productivity",
        "Engineering Productivity & Quality",
        ("ci/cd", "code quality", "testing", "dev tools"),
    ),
)

# Keyed by domain slug → (display_name, keywords). Used for O(1) lookup
# when frontmatter names a domain explicitly.
_DOMAIN_BY_SLUG: dict[str, tuple[str, tuple[str, ...]]] = {
    slug: (title, keywords) for slug, title, keywords in _DOMAINS
}

# Slug-prefix → expected canonical domain. Tiebreaker hint when keyword
# inference is ambiguous and a sanity signal the validator surfaces when
# the agent set an obviously wrong domain (v11-U8 — Cycle 10 audit found
# `seller-bl-api-optimization` shipped with `domain: buyer-experience`).
# Every value MUST be a key of `_DOMAIN_BY_SLUG`; drift would silently
# misroute pages.
_SLUG_PREFIX_DOMAIN: dict[str, str] = {
    # Seller-facing pages
    "seller-": "seller-experience",
    "bl-": "seller-experience",  # BL = buy-lead (a seller acquisition product)
    "buylead-": "seller-experience",
    "lms-": "seller-experience",
    # Buyer-facing pages
    "buyer-": "buyer-experience",
    "buyermy-": "buyer-experience",
    # Discovery / marketplace
    "mcat-": "marketplace-discovery",
    "isq-": "marketplace-discovery",
    "categoriz-": "marketplace-discovery",
    # Engineering productivity
    "gladmin-": "engineering-productivity",
    "ci-": "engineering-productivity",
    "cd-": "engineering-productivity",
}
# Defensive guard: reject any prefix that doesn't map to a canonical
# domain. Cheaper to fail at import than to silently route a page to a
# nonexistent hub.
_bad_prefix_domains = {
    domain for domain in _SLUG_PREFIX_DOMAIN.values() if domain not in _DOMAIN_BY_SLUG
}
if _bad_prefix_domains:
    raise RuntimeError(
        f"_SLUG_PREFIX_DOMAIN values must be canonical domain slugs; "
        f"unknown: {sorted(_bad_prefix_domains)}"
    )


def _domain_from_slug_prefix(slug: str) -> str | None:
    """Return the expected domain for a page slug, or None if no prefix matches.

    Longest-prefix-match wins so `buyermy-` beats `buyer-`. Used as a
    tiebreaker by `_assign_domains` and as a sanity check by the
    validator (`check_domain_prefix_mismatch`).
    """
    for prefix in sorted(_SLUG_PREFIX_DOMAIN, key=len, reverse=True):
        if slug.startswith(prefix):
            return _SLUG_PREFIX_DOMAIN[prefix]
    return None


def _infer_domain_from_keywords(title: str, body: str) -> str | None:
    """Return the first domain slug whose keyword list hits title+body.

    Body is scanned to its first paragraph only (via `_first_paragraph`) —
    scanning the full body let noise from "related" sections win over the
    page's actual subject. First-match wins for determinism; `_DOMAINS`
    order is the tie-breaker.
    """
    haystack = f"{title}\n{_first_paragraph(body)}".lower()
    for slug, _title, keywords in _DOMAINS:
        for kw in keywords:
            if kw in haystack:
                return slug
    return None


def _assign_domains(fm: dict[str, Any], body: str, slug: str = "") -> list[str]:
    """Decide which domain hub(s) a page belongs to.

    `slug` is the page's filename stem (or frontmatter `slug:`); it
    powers the v11-U8 prefix tiebreaker. Empty string disables it.

    Preference order (per North Star):
    1. Explicit `domains:` list frontmatter — v10-U2 multi-value form;
       every canonical slug in the list attaches the page (a topic that
       spans e.g. trust-safety + growth-monetization shows up on both
       hubs). When any non-empty `domains:` list is present, it is
       authoritative: even a list of entirely non-canonical entries
       still blocks the fallthrough so the validator's `unknown-domain-
       value` warnings match the viewer's & rollup's rendering. Matches
       `_render_domain_badges` / `_extract_domain_values` precedence.
    2. Explicit `domain:` frontmatter — trusted verbatim (1 hub). The
       slug-prefix sanity check lives in the validator, not here, so the
       compiler never silently overrides a human/agent decision.
    3. `tags:` list — every tag matching a domain slug attaches the page.
    4. Keyword match against the page title + first paragraph — transitional
       fallback. Callers aggregate the inferred count and emit a single
       summary log per pass (see `_regenerate_domain_hubs`,
       `_bucket_pages_by_domain`) instead of per-page noise.
    5. Slug-prefix tiebreaker (v11-U8) — when keyword inference fell
       through without a hit, the slug prefix may still tag the page.
       Only fires AFTER explicit input is exhausted; prefix is a hint,
       never an override. `seller-bl-api-optimization` would land on
       `seller-experience` instead of `[]`.
    """
    plural = fm.get("domains")
    if isinstance(plural, list) and plural:
        # Any non-empty list is authoritative: if every entry is bogus we
        # still return `[]` rather than fall through to `domain:` /
        # keyword inference, matching the validator + renderer.
        return [v for v in plural if isinstance(v, str) and v in _DOMAIN_BY_SLUG]

    explicit = fm.get("domain")
    if isinstance(explicit, str) and explicit in _DOMAIN_BY_SLUG:
        return [explicit]

    tags = fm.get("tags") or []
    if isinstance(tags, list):
        tag_hits = [t for t in tags if isinstance(t, str) and t in _DOMAIN_BY_SLUG]
        if tag_hits:
            return tag_hits

    title = str(fm.get("title", ""))
    inferred = _infer_domain_from_keywords(title, body)
    if inferred:
        return [inferred]

    # Tiebreaker: only consult slug-prefix when keyword inference came
    # up empty. Conservative — never overrides an inferred hit. The
    # validator owns the louder "your explicit `domain:` disagrees with
    # your slug" warning so we don't mangle an agent's deliberate choice.
    prefix_hint = _domain_from_slug_prefix(slug) if slug else None
    if prefix_hint:
        return [prefix_hint]
    return []


def _was_domain_inferred(fm: dict[str, Any]) -> bool:
    """Return True iff `_assign_domains` would fall through to keyword inference.

    A page "needs the inference fallback" when none of `domains:` (non-empty
    list), `domain:`, or `tags:` surfaces a canonical domain slug. Pure
    helper used by callers that want to emit a single aggregate log per
    pass rather than per page (the per-page log generated duplicate lines
    when the same page drove multiple rollups).
    """
    plural = fm.get("domains")
    if isinstance(plural, list) and plural:
        return False
    explicit = fm.get("domain")
    if isinstance(explicit, str) and explicit in _DOMAIN_BY_SLUG:
        return False
    tags = fm.get("tags") or []
    return not (
        isinstance(tags, list) and any(isinstance(t, str) and t in _DOMAIN_BY_SLUG for t in tags)
    )
