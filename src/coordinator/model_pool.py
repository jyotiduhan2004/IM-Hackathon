"""Model-pool fallback helpers for the compile coordinator.

The compile run picks a random model from the configured pool for each
batch. This module is responsible for:

- discovering which models the LiteLLM proxy actually advertises
- quarantining models with bad recent ``compile_attempts`` health
- recognising provider-side "this model is dead" signals so we can drop
  the model from the pool and retry the batch

Imports here are intentionally narrow: ``SilentModelFailError`` is lazy-
imported inside ``_is_model_unavailable_error`` so importing the
coordinator helpers doesn't drag the whole compiler module along.
"""

from __future__ import annotations

import os
from typing import Any

import click
import httpx
import psycopg
import structlog

from src.config import settings
from src.db.messages import model_health_stats

logger = structlog.get_logger(__name__)


# Auto-exclusion thresholds. If you tune these, update the matching
# comment block in src/config.py::llm_model_pool so future reviewers can
# reason about "why did my model get dropped?" without grepping.
#
# Two windows, both gated by _HEALTH_MIN_ATTEMPTS:
# - 24h window: the historical guard — catches persistent offenders with
#   a moderate threshold (>50% fail OR >=10 absolute failures).
# - 4h window: aggressive short-window quarantine — catches "hot" breakage
#   (LiteLLM proxy starts 400-ing a model mid-day) before the 24h window
#   dilutes the signal with earlier successes. Threshold is higher (>80%)
#   because 4h is noisy; we only pull the trigger when the model is
#   clearly broken *right now*.
_HEALTH_WINDOW_HOURS = 24
_HEALTH_SHORT_WINDOW_HOURS = 4
_HEALTH_MIN_ATTEMPTS = 5
_HEALTH_FAIL_RATE_THRESHOLD = 0.5
_HEALTH_SHORT_WINDOW_FAIL_RATE_THRESHOLD = 0.80
_HEALTH_ABS_FAILURE_CAP = 10


def _fetch_available_models() -> set[str] | None:
    """Return the LiteLLM proxy's advertised model ids, or None on failure."""
    if not settings.litellm_base_url or not settings.openai_api_key:
        return None

    headers = {"Authorization": f"Bearer {settings.openai_api_key}"}
    url = settings.litellm_base_url.rstrip("/") + "/models"
    try:
        response = httpx.get(url, headers=headers, timeout=5)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("model_catalog_fetch_failed", url=url, error=str(exc))
        return None

    try:
        payload = response.json()
    except ValueError as exc:
        logger.warning("model_catalog_invalid_json", url=url, error=str(exc))
        return None

    data = payload.get("data")
    if not isinstance(data, list):
        logger.warning("model_catalog_unexpected_shape", url=url, payload_type=type(data).__name__)
        return None

    return {
        model_id.strip()
        for item in data
        if isinstance(item, dict)
        and isinstance(model_id := item.get("id"), str)
        and model_id.strip()
    }


def _filter_pool_to_available_models(
    pool: list[str], available_models: set[str]
) -> tuple[list[str], list[str]]:
    """Drop pool entries the proxy does not currently advertise."""
    kept = [model for model in pool if model in available_models]
    dropped = [model for model in pool if model not in available_models]
    return kept, dropped


def _healthy_pool(pool: list[str]) -> tuple[list[str], list[dict[str, Any]]]:
    """Filter ``pool`` by recent ``compile_attempts`` outcomes.

    Two-window quarantine:
    - 24h window (persistent): drops models where
      ``(fail_rate > 0.5 AND total >= 5) OR failed_hard >= 10``.
      Catches models that have drifted broken over the day.
      ``failed_hard`` (real failures, excluding timeouts) gates the
      absolute cap so a burst of proxy stalls doesn't nuke an otherwise
      healthy model (#194: 24 grok timeouts had the cap excluding our
      best performer). Timeouts still feed ``fail_rate``, so a model
      that's consistently slow still gets caught by the rate guard.
    - 4h window (short): drops models where
      ``fail_rate > 0.80 AND total >= 5``. Catches "hot" breakage that
      hasn't accumulated enough 24h signal yet (e.g. LiteLLM proxy starts
      400-ing a model an hour ago). Higher threshold because 4h is noisy.

    Fails open — on DB errors, missing stats, or a filter that would
    empty the pool, returns the unfiltered pool (with a warning when all
    models would be excluded, so we never deadlock).

    Returns ``(kept_models, exclusion_records)``. Each exclusion record
    is a ``model_health_stats`` dict augmented with ``reason`` and
    ``window_hours`` so the caller can tell the operator which guard
    fired. If a model trips both windows, the 24h record wins (persistent
    offenders are the more damning signal).
    """
    long_stats: dict[str, dict[str, Any]] = {}
    short_stats: dict[str, dict[str, Any]] = {}
    try:
        long_stats = {
            s["compile_model"]: s for s in model_health_stats(since_hours=_HEALTH_WINDOW_HOURS)
        }
        short_stats = {
            s["compile_model"]: s
            for s in model_health_stats(since_hours=_HEALTH_SHORT_WINDOW_HOURS)
        }
    except psycopg.Error as exc:
        logger.warning("healthy_pool_db_error", error=str(exc))
        return pool, []

    kept: list[str] = []
    excluded: list[dict[str, Any]] = []
    for m in pool:
        long = long_stats.get(m)
        short = short_stats.get(m)
        long_drop = long is not None and (
            (
                long["fail_rate"] > _HEALTH_FAIL_RATE_THRESHOLD
                and long["total"] >= _HEALTH_MIN_ATTEMPTS
            )
            or long["failed_hard"] >= _HEALTH_ABS_FAILURE_CAP
        )
        short_drop = (
            short is not None
            and short["fail_rate"] > _HEALTH_SHORT_WINDOW_FAIL_RATE_THRESHOLD
            and short["total"] >= _HEALTH_MIN_ATTEMPTS
        )
        # Narrowing note: mypy can't propagate the `is not None` guard from
        # `*_drop` (compound-bool local) into the branch body, so we re-assert.
        if long_drop:
            assert long is not None
            excluded.append({**long, "reason": "quarantined (24h)", "window_hours": 24})
        elif short_drop:
            assert short is not None
            excluded.append({**short, "reason": "quarantined (4h)", "window_hours": 4})
        else:
            kept.append(m)

    if not kept:
        logger.warning("healthy_pool_would_empty_pool", excluded=excluded)
        return pool, excluded
    return kept, excluded


def _is_model_unavailable_error(exc: BaseException) -> bool:
    """True if ``exc`` indicates the LiteLLM proxy refuses this model for
    this team key OR the upstream provider is transiently dead. Covers:

    - 401 ``team not allowed to access model`` — original LiteLLM shape
    - 400 ``Invalid model name`` — original LiteLLM shape
    - 401 ``Authentication Error`` — bare auth fail (Bug K, Cycle 6 glm-5
      at 162s died with ``Error code: 401 - {'error': {'message':
      'Authentication Error'...`` and slipped past the original string
      match)
    - 403 ``Forbidden`` — bare forbidden (Bug K)
    - ``SilentModelFailError`` — HTTP 200 with empty payload (Bug J,
      docs/audits/cycle-5-case-bug-j-minimax-silent-fail.md)
    - ``StuckLLMRoundError`` / ``InvokeWallClockTimeout`` — heartbeat or
      wall-clock fired; pool retry gives next model a fresh chance.
    - 5xx HTML error pages from the proxy front-end. 2026-04-24 smoke:
      glm-5.1 hit 5/29 ``<title>502 Server Error</title>`` HTML responses
      (each at ~570 s) — these are upstream provider gateway failures
      raised as opaque text bodies that don't follow the
      ``Error code: NNN`` pattern. Treat as infra so the batch retries
      with another pool model instead of burning the round.
    - ``Request timed out.`` / ``OpenrouterException`` — provider wedge.

    All are infrastructure failures, not agent failures; the batch
    should retry with a different pool model instead of being marked
    failed. The 24h ``_healthy_pool`` guard can't help here because the
    failure must accumulate over time and every batch in between burns
    latency + telemetry rows.

    False-positive hedge: ``Error code: 401`` / ``Error code: 403`` is
    LiteLLM's structured error prefix — it doesn't appear in normal
    tool output or model text. The HTML matchers anchor on the
    ``<title>...</title>`` tag, which a wiki page body wouldn't carry
    in an exception string.
    """
    from src.agent.reviewer_result import SilentModelFailError
    from src.agent.runtime import InvokeWallClockTimeout
    from src.agent.runtime import StuckLLMRoundError

    if isinstance(exc, SilentModelFailError | StuckLLMRoundError | InvokeWallClockTimeout):
        return True
    msg = str(exc)
    if "team not allowed to access model" in msg or "Invalid model name" in msg:
        return True
    if "Error code: 401" in msg or "Error code: 403" in msg:
        return True
    # Structured 5xx (LiteLLM occasionally surfaces gateway errors this way)
    if "Error code: 502" in msg or "Error code: 503" in msg or "Error code: 504" in msg:
        return True
    if "Request timed out." in msg or "OpenrouterException - Provider returned" in msg:
        return True  # provider wedge / LiteLLM no-fallback (smoke 02c9d536)
    # HTML 5xx pages bubbled up as the exception body — Google Frontend /
    # nginx / Cloudflare all use the same ``<title>NNN ...</title>`` shape.
    # Note ``Gateway Time-out`` (with hyphen): nginx's stock 504 page uses
    # the hyphenated form, distinct from Cloudflare/Google's ``Timeout``.
    return (
        "<title>502 Server Error</title>" in msg
        or "<title>503 Service Unavailable</title>" in msg
        or "<title>504 Gateway Timeout</title>" in msg
        or "<title>504 Gateway Time-out</title>" in msg
        or "<title>502 Bad Gateway</title>" in msg
    )


def _setup_model_pool(model_pool: str | None, resolved_model: str) -> list[str]:
    """Parse --model-pool, tracking source for diagnosis.

    CLI flag overrides settings.model_pool. Empty final list = no pool (use
    `resolved_model` for every batch); a list = sample one at random per batch.

    `pool_source` is load-bearing for diagnosis: the last-4h trace showed a
    known-broken model appearing via env/CLI override even though src/config.py
    had dropped it from the default. The run-start log line lets us see
    immediately whether the pool came from the default list, LLM_MODEL_POOL
    env var, or a CLI flag.
    """
    pool: list[str]
    pool_source: str
    if model_pool is not None:
        pool = [m.strip() for m in model_pool.split(",") if m.strip()]
        pool_source = "cli:--model-pool"
    else:
        pool = settings.model_pool if len(settings.model_pool) > 1 else []
        # pydantic-settings fills `llm_model_pool` from env when set, otherwise
        # from the class default. Presence of LLM_MODEL_POOL in os.environ is
        # the cheapest, most reliable distinguisher.
        pool_source = "env:LLM_MODEL_POOL" if "LLM_MODEL_POOL" in os.environ else "default"
    logger.info(
        "effective_model_pool", pool=pool, source=pool_source, resolved_model=resolved_model
    )
    return pool


def _prepare_model_pool(
    pool: list[str], available: set[str] | None, base_url: str | None, resolved_model: str
) -> list[str]:
    """Filter pool by provider-catalog availability, then drop quarantined models.

    Echoes diagnostics via click.echo so operators see what got dropped and why.
    Returns the (possibly shorter) pool ready for per-batch sampling.
    """
    if pool and available is not None:
        pool, unavailable = _filter_pool_to_available_models(pool, available)
        if unavailable:
            click.echo("Provider catalog dropped " + ", ".join(unavailable) + " (not in /models)")
    if not pool and available is not None and base_url and resolved_model not in available:
        click.echo(
            f"WARNING: selected model {resolved_model} is not advertised by "
            f"{base_url.rstrip('/')}/models"
        )

    # Drop chronically-failing models from the pool at run-start so we don't
    # spend a run rediscovering the same 401/400/recursion-loop failure. Fails
    # open on DB errors so a Postgres blip can't block compile. See
    # `_healthy_pool` for the rule.
    #
    # Note: this is short-window *quarantine*, not permanent removal. Permanent
    # removal from the default pool lives in `src/config.py`. A quarantined
    # model re-appears next run if the window has cleared — a config-removed
    # model does not.
    if pool:
        pool, excluded = _healthy_pool(pool)
        if excluded:
            click.echo(
                "Auto-exclusion dropped "
                + ", ".join(
                    f"{s['compile_model']} [{s.get('reason', 'quarantined')}] "
                    f"({s['failed']}/{s['total']} failed)"
                    for s in excluded
                )
            )
    if pool:
        click.echo(f"Model pool: {pool} (random pick per batch)")
    return pool


def _refresh_pool_for_batch(
    initial_pool: list[str],
    unauthorized: set[str],
    announced_quarantines: set[tuple[str, str]],
    batch_idx: int,
) -> list[str]:
    """Re-filter run-start pool against fresh ``compile_attempts`` (#194).

    Filter from ``initial_pool`` (not previously filtered ``pool``) so
    quarantined models can recover mid-run. ``unauthorized`` carries 401/403
    prunes. ``announced_quarantines`` dedupes the log line per (model, reason).
    """
    eligible = [m for m in initial_pool if m not in unauthorized]
    if not eligible:
        return []
    pool, excluded = _healthy_pool(eligible)
    # _healthy_pool fails open (returns full input + exclusion records) when
    # filtering would empty the pool. Don't announce a model that's still in
    # the returned set — that's a false alarm. Claude review on PR #252.
    returned_set = set(pool)
    for r in excluded:
        if r["compile_model"] in returned_set:
            continue
        key = (r["compile_model"], r.get("reason", "quarantined"))
        if key in announced_quarantines:
            continue
        announced_quarantines.add(key)
        logger.info(
            "mid_run_quarantine",
            model=key[0],
            reason=key[1],
            batch_idx=batch_idx,
            fail_rate=r.get("fail_rate"),
            total=r.get("total"),
            failed=r.get("failed"),
        )
        click.echo(
            f"  mid-run auto-exclusion: {key[0]} [{key[1]}] "
            f"({r.get('failed')}/{r.get('total')} failed) — batch {batch_idx}"
        )
    return pool
