# Issue: Data Model — Raw Email Format, Wiki Page Format, Relations

**Labels**: `documentation`, `architecture`, `phase-0`

---

## Overview

This issue documents the exact format of every file the system produces. Two file types:
**raw emails** (immutable input) and **wiki pages** (LLM-maintained output). Both are
markdown with YAML frontmatter. Relations between them (supersession, conflicts) are
expressed via frontmatter fields and inline references.

## 1. Raw Email Format (`raw/`)

Each email becomes one `.md` file in `raw/`. These files are **never modified** after creation
(except the `compiled: true` flag).

### Naming Convention

```
raw/YYYY-MM-DD_{subject-slug}_{msg-id-short}.md
```

- `YYYY-MM-DD` — email send date
- `subject-slug` — lowercase, hyphens, max 50 chars (Re:/Fwd: prefixes stripped)
- `msg-id-short` — first 8 chars of SHA-256 hash of the full Message-ID

Examples:
```
raw/2026-04-01_q1-sales-review-results_a3f8c2d1.md
raw/2026-04-05_updated-reimbursement-policy_7b2e9f04.md
raw/2026-04-10_updated-reimbursement-policy_c1d4a8e2.md
```

### YAML Frontmatter

```yaml
---
message_id: "<CABx123abc@mail.gmail.com>"
thread_id: "18f2a3b4c5d6e7f8"
subject: "Updated Reimbursement Policy"
from: "Jane Doe <jane@company.com>"
to:
  - "all-hands@company.com"
cc:
  - "finance@company.com"
date: "2026-04-05T14:30:00+05:30"
in_reply_to: "<CABx789xyz@mail.gmail.com>"
labels:
  - "INBOX"
  - "policy"
has_attachments: true
attachment_files:
  - "raw/attachments/a3f8c2d1/reimbursement-policy-v2.pdf"
inline_images:
  - path: "raw/attachments/a3f8c2d1/comparison-chart.png"
    caption: "Bar chart comparing old vs new reimbursement limits by category"
ingested_at: "2026-04-11T10:00:00Z"
compiled: false
---
```

### Rules

1. Raw files are **immutable** — never edited after creation (except `compiled` flag)
2. The `compiled` field starts as `false`, set to `true` after successful compilation
3. The `in_reply_to` field enables thread reconstruction
4. Attachment files are stored in `raw/attachments/{msg-id-short}/`
5. Inline images get a `caption` field populated by a vision model at ingest time

## 2. Wiki Page Format (`wiki/`)

Wiki pages are created and maintained by the LLM compiler.

### Directory Structure

```
wiki/
├── index.md              # Master catalog — list of all pages by category
├── log.md                # Append-only chronological log
├── topics/               # Projects, products, initiatives
├── entities/             # People, teams, products, systems
├── policies/             # Current policies with version history
├── timelines/            # Long-running topic chronologies
└── conflicts/            # Unresolved contradictions
```

### YAML Frontmatter

```yaml
---
title: "Reimbursement Policy"
page_type: policy            # topic | entity | policy | timeline | conflict
status: current              # current | superseded | contested
sources:
  - "raw/2026-03-15_reimbursement-policy-announcement_d4e5f6a7.md"
  - "raw/2026-04-05_updated-reimbursement-policy_7b2e9f04.md"
supersedes:
  - page: "wiki/policies/reimbursement-policy-v1.md"
    reason: "April 5 email explicitly states 'this supersedes the March 15 policy'"
    superseded_on: "2026-04-05"
related:
  - "[[finance-team]]"
  - "[[q1-sales-review]]"
last_compiled: "2026-04-11T10:05:00Z"
---
```

### Page Types

| Type | Directory | When to create |
|---|---|---|
| `topic` | `wiki/topics/` | A project, initiative, or theme |
| `entity` | `wiki/entities/` | A person, team, product, or system |
| `policy` | `wiki/policies/` | A rule/procedure/guideline with history |
| `timeline` | `wiki/timelines/` | Chronological log for a long-running topic |
| `conflict` | `wiki/conflicts/` | Emails that contradict each other |

### Status Values

| Status | Meaning |
|---|---|
| `current` | Active, up-to-date |
| `superseded` | A newer page/email has replaced this |
| `contested` | Conflicting information — needs human review |

## 3. Relations

### Supersession Example

**On the new page**:
```yaml
supersedes:
  - page: "wiki/policies/reimbursement-policy-v1.md"
    reason: "April 5 email explicitly states 'this supersedes the March 15 policy'"
    superseded_on: "2026-04-05"
```

**On the old page**:
```yaml
status: superseded
superseded_by:
  page: "wiki/policies/reimbursement-policy.md"
  reason: "Replaced by updated policy on April 5"
  superseded_on: "2026-04-05"
```

### Conflict Example

```yaml
---
title: "Conflict: Reimbursement Submission Deadline"
page_type: conflict
status: contested
parties:
  - source: "raw/2026-04-05_updated-reimbursement-policy_7b2e9f04.md"
    claim: "Submission deadline is 15 days"
  - source: "raw/2026-04-08_reimbursement-deadline-extension_e5f6a7b8.md"
    claim: "Submission deadline extended to 21 days for field staff"
resolution: pending
---

## Position A (April 5)
...

## Position B (April 8)
...

## Analysis
Position B may be an exception to Position A rather than a contradiction.
Requires confirmation from Finance or Ops leadership.
```

## 4. Index and Log

### `wiki/index.md` — Master catalog, regenerated after every compilation

```markdown
# Knowledge Base Index

Last updated: 2026-04-11T10:05:00Z
Total pages: 23

## Policies (3)
- [[reimbursement-policy]] — Current (April 5, 2026)
- [[travel-approval-process]] — Current
- [[vendor-onboarding-checklist]] — Current

## Topics (8)
- [[q1-sales-review]]
- [[product-launch-alpha]]
...

## Entities (10)
- [[jane-doe]]
- [[product-alpha]]
...
```

### `wiki/log.md` — Append-only chronological record

```markdown
# Compilation Log

| Timestamp | Event |
|---|---|
| 2026-04-11T10:05:00Z | INGEST: 3 emails from 2026-04-05 to 2026-04-08 |
| 2026-04-11T10:05:15Z | COMPILE: Updated reimbursement-policy, jane-doe |
| 2026-04-11T10:05:20Z | CREATE: Conflict page reimbursement-submission-deadline |
| 2026-04-11T10:05:25Z | INDEX: Updated index.md (23 pages) |
```
