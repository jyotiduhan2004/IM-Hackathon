-- Add 'insufficient_decision' to compile_insights category CHECK list.
-- Pairs with src/compile/compiler.py::_VALID_INSIGHT_CATEGORIES +
-- src/compile/middleware/terminal_decision_guard.py.
--
-- V12 50-compile deep audit 2026-04-23 (§7 Tier 2 #5) surfaced batch
-- 45 (kimi-k2.6) exiting with ``turns=6 tools=8 writes=0`` and no
-- terminal log_insight. The agent read the email + a candidate page,
-- decided nothing, then exited. Email stayed ``pending`` and got
-- re-queued next cycle — wasted cost, silent failure.
--
-- The terminal-decision guard middleware enforces the existing
-- "every email gets a terminal outcome" rule at the runtime layer.
-- ``insufficient_decision`` is the dedicated escape hatch for the
-- case the audit flagged: substantive email, NOT captured elsewhere,
-- no obvious target page. Without this category the agent either
-- fabricated a bad topic page or exited silently; with it, the skip
-- is recorded with a distinct reason humans can grep in triage.
--
-- The coordinator (scripts/compile_all.py::_SKIP_INSIGHT_CATEGORIES)
-- treats this as a terminal skip — same flow as ``trivial_skip`` and
-- ``already_captured``, the message flips to ``skipped`` state so it
-- is never re-queued. Idempotent via DROP CONSTRAINT IF EXISTS.
ALTER TABLE compile_insights DROP CONSTRAINT IF EXISTS compile_insights_category_check;
ALTER TABLE compile_insights ADD CONSTRAINT compile_insights_category_check
  CHECK (category IN (
    'topic_merge_candidate','question_for_human','prompt_ambiguity',
    'tool_gap','supersession_doubt','structure_suggestion','trivial_skip',
    'already_captured','insufficient_decision'
  ));
