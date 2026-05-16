-- Add 'already_captured' to compile_insights category CHECK list.
-- Pairs with src/compile/compiler.py::_VALID_INSIGHT_CATEGORIES.
--
-- Semantic split vs trivial_skip: trivial_skip = non-substantive email
-- (one-line "Yes, please", OOO auto-reply). already_captured =
-- substantive email (real stats / decisions / dates) whose facts are
-- already merged onto the existing topic page by a prior message in
-- the same thread; no new page delta needed.
--
-- Also includes trivial_skip in the new CHECK list so fresh schema
-- installs that haven't yet picked up the trivial_skip fix land in
-- the same final state. Idempotent via DROP CONSTRAINT IF EXISTS.
ALTER TABLE compile_insights DROP CONSTRAINT IF EXISTS compile_insights_category_check;
ALTER TABLE compile_insights ADD CONSTRAINT compile_insights_category_check
  CHECK (category IN (
    'topic_merge_candidate','question_for_human','prompt_ambiguity',
    'tool_gap','supersession_doubt','structure_suggestion','trivial_skip',
    'already_captured'
  ));
