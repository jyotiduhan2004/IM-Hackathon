"""Repository for the compile_tool_calls table.

One row per tool invocation inside a compile run. `insert_many` bulk-inserts
a batch's buffered records at the end of each batch; `summarize` produces the
`top_tools=…` string the coordinator appends to the log row. `fallback_to_jsonl`
writes the same payload to `docs/audits/tool_calls-<run_id>.jsonl` when the DB
is unreachable so telemetry never silently drops.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.db import connect


def insert_many(run_id: str, records: list[dict[str, Any]]) -> int:
    """Bulk-insert tool-call records for one batch. Returns count inserted.

    `records` is the coordinator's dict-cast of `ToolCallRecord` TypedDicts.
    Times are POSIX floats (seconds) — `to_timestamp(...)` converts at DB
    write. Missing optional fields default to NULL.
    """
    if not records:
        return 0
    count = 0
    with connect() as conn, conn.transaction():
        for r in records:
            conn.execute(
                """
                INSERT INTO compile_tool_calls (
                  run_id, tool_name, inputs_json, output_preview,
                  output_bytes, latency_ms, status, error_message,
                  started_at, finished_at
                ) VALUES (%s, %s, %s::jsonb, %s, %s, %s, %s, %s,
                          to_timestamp(%s), to_timestamp(%s))
                """,
                (
                    run_id,
                    r["tool_name"],
                    r.get("inputs_json"),
                    r.get("output_preview"),
                    r.get("output_bytes"),
                    r.get("latency_ms"),
                    r.get("status"),
                    r.get("error_message"),
                    r.get("started_at"),
                    r.get("finished_at"),
                ),
            )
            count += 1
    return count


def summarize(run_id: str) -> dict[str, Any]:
    """Roll up tool-call stats for one run. Used for the batch-log `top_tools=…`."""
    with connect() as conn:
        raw_rows = conn.execute(
            """
            SELECT tool_name, count(*)::int AS calls,
                   avg(latency_ms)::int AS avg_ms,
                   sum(CASE WHEN status='error' THEN 1 ELSE 0 END)::int AS errors
              FROM compile_tool_calls
             WHERE run_id = %s
             GROUP BY tool_name
             ORDER BY calls DESC
            """,
            (run_id,),
        ).fetchall()
    # `connect()` uses dict_row, so rows are dicts at runtime even though
    # psycopg's default type stub is tuple-ish. Local retype silences mypy
    # without polluting every access site with a cast.
    rows: list[dict[str, Any]] = list(raw_rows)  # type: ignore[arg-type]
    return {
        "top_by_count": [(r["tool_name"], r["calls"]) for r in rows[:5]],
        "top_by_latency": sorted(
            [(r["tool_name"], r["avg_ms"]) for r in rows if r["avg_ms"]],
            key=lambda x: -x[1],
        )[:5],
        "total_calls": sum(r["calls"] for r in rows),
        "total_errors": sum(r["errors"] for r in rows),
    }


def fallback_to_jsonl(run_id: str, records: list[dict[str, Any]]) -> Path:
    """Append records to `docs/audits/tool_calls-<run_id>.jsonl`.

    Used when the DB insert raises — we don't want to lose telemetry just
    because Postgres is down. File is append-only so repeated flushes for
    the same run concatenate.
    """
    audits_dir = Path("docs/audits")
    audits_dir.mkdir(parents=True, exist_ok=True)
    out_path = audits_dir / f"tool_calls-{run_id}.jsonl"
    with out_path.open("a", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, default=str) + "\n")
    return out_path
