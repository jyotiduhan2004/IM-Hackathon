# Issue: Build Lint Agent — Wiki Health Checks

**Labels**: `feature`, `phase-0`

---

## Overview

Scan the wiki for quality issues: stale content, contradictions the compiler missed,
orphan pages, missing cross-references, and data gaps.

## Status: Implemented in Phase 0 (basic checks, auto-fix comes in Phase 2)

Files:
- `scripts/lint_wiki.py` — deterministic check runner, Click CLI

## Checks implemented

| Check | Severity | Auto-fixable |
|---|---|---|
| Missing frontmatter | error | no |
| Incomplete frontmatter (missing required fields) | error | no |
| Invalid status value | error | no |
| Invalid page_type | error | no |
| Broken wikilinks (point to non-existent pages) | warning | no (Phase 2) |
| Orphan pages (not linked anywhere) | warning | yes (Phase 2) |
| Missing index entries | info | yes (Phase 2) |

## Usage

```bash
uv run python scripts/lint_wiki.py              # report only
uv run python scripts/lint_wiki.py --fix        # auto-fix (not yet implemented)
uv run python scripts/lint_wiki.py --category orphan  # check one category
```

## Output example

```
Wiki Lint Report — 5 issues
============================================================

ERRORS (2):
  ✗ [incomplete_frontmatter] wiki/topics/q1-review.md
    Missing required fields: ['last_compiled', 'status']
  ✗ [invalid_status] wiki/policies/travel.md
    Invalid status 'old' (must be one of {'current', 'superseded', 'contested'})

WARNINGS (2):
  ⚠ [broken_link] wiki/topics/product-alpha.md
    Broken wikilink [[launch-timeline]] — page does not exist
  ⚠ [orphan] wiki/entities/raj-kumar.md
    Not linked from any other page or index
    Auto-fixable: yes

INFO (1):
  ℹ [missing_index_entry] wiki/topics/new-initiative.md
    Page 'new-initiative' not listed in index.md
    Auto-fixable: yes

Summary: 2 errors, 2 warnings, 1 info. 2 auto-fixable.
```

## Future work (Phase 2)

- LLM-powered checks: detect missed contradictions, missing cross-references by semantic match
- Stale page detection: compare last_compiled to newer raw emails referencing same topic
- Unresolved conflict timeout: flag conflict pages open > 7 days
- Auto-fix implementation for safe issues (regenerate index, add cross-refs)
- Human review queue for low-confidence fixes

## Acceptance criteria

- [x] `uv run python scripts/lint_wiki.py` scans wiki and reports issues
- [x] 7 basic check categories implemented (frontmatter, status, page_type, broken links, orphans, index)
- [x] Clear output with severity levels
- [x] Returns non-zero exit code if errors found
- [ ] `--fix` flag auto-fixes safe issues (Phase 2)
- [ ] LLM-powered semantic checks (Phase 2)
