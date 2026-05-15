# Article Structure Template — Reference

## Frontmatter Template
```yaml
---
title: "Human-Readable Title"
page_type: topic
domain: marketplace-discovery
status: active
tags: [tag1, tag2]
aliases: ["Abbreviation", "Alt Name"]
summary: "One-line summary for agent quick scanning"
owner: "[[person-slug]]"
related:
  - related-page-1
  - related-page-2
source_threads: ["thread-id-1", "thread-id-2"]
last_compiled: "2026-01-15T10:30:00+05:30"
updated_by: model-name
update_count: 1
---
```

## Page Body Template
```markdown
# Title

2-4 sentence lead paragraph. What this is, why it matters, current status.
First occurrence of [[glossary-term]] linked. First [[related-page]] (brief description of what it covers) linked with context.

## Overview
Context for newcomers. Assume zero prior knowledge.
Explain what problem this solves and who it's for.

## Key Details
Organized by SUBTOPIC, not by email/date.

### Subtopic A
Details...

### Subtopic B
Details...

## Current Status
- What's live and at what rollout percentage
- Key metrics (conversion, latency, error rates)
- What's in pilot or upcoming

## Decisions & Changes
| Date | Decision | By |
|------|----------|------|
| 2026-02-15 | Expanded to 40% traffic | [[person]] |

## Open Questions
- Unresolved item 1
- Unresolved item 2

## Related Pages
- [[page-a]] — brief description
- [[page-b]] — brief description

## Sources
[^msg-abc]: raw/2026-01-15_topic_abc12345.md
```

## Section Rules
- Lead paragraph and Key Details are MANDATORY
- Sources is MANDATORY (but keep light — not every sentence needs a citation)
- All other sections: include ONLY if relevant content exists
- Never create empty sections
