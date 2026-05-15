---
name: merge-detection
description: "Load when deciding whether a new email topic should create a new wiki page or merge into an existing one. Uses Wikipedia's primary topic rule and >50% overlap merge threshold."
metadata:
  type: compile
  version: 1.0
---

## Decision Framework (Wikipedia Rules Applied to IndiaMART)

When a new email arrives, follow this decision tree:

### Step 1: Find Existing Pages
Call resolve_page() and list_wiki_pages() to check if a related page already exists.

### Step 2: Apply Primary Topic Rule
The email SUBJECT determines the primary page:
- "Seller ISQ on WhatsApp" → primary topic is **Seller ISQ** (WhatsApp is context)
- "WhatsApp 9696 Bot Update" → primary topic is **WhatsApp 9696**
- "AI-Powered Code Review for SCM Pipeline" → primary topic is **AI-Powered Code Review**

The primary topic = the noun phrase that the email is fundamentally ABOUT, not the context or channel.

### Step 3: Evaluate Overlap
Compare the new email's content against the candidate existing page:
- **>50% overlap** → MERGE into the existing page (update relevant sections)
- **20-50% overlap** → MERGE but also add cross-reference links to other related pages
- **<20% overlap** → CREATE a new page

### Step 4: Check for Page Families
If 3+ pages cover variants of the same concept (e.g., WhatsApp 8181, WhatsApp 9696, WhatsApp general):
- Ensure a **parent hub page** exists that links to all variants
- The hub page should have a brief comparison/overview, not duplicate content

### Step 5: Handle Aliases
If the new email uses a different name for an existing topic:
- Do NOT create a new page
- MERGE into the existing page
- Add the alternate name to the `aliases` field in frontmatter

## Rules

1. **No notability threshold.** Every distinct topic gets a page, even from 1 email. A thin page that grows is better than a gap.
2. **Primary topic wins.** Email content goes to the page whose title best matches the email subject.
3. **Merge >50%.** If more than half the content would duplicate an existing page, merge.
4. **Parent hubs for families.** When 3+ pages form a family, create a hub.
5. **Aliases for alt names.** Different name = same page + alias, not new page.

## Gotchas
- An email about "testing ISQ on WhatsApp 9696" could match both ISQ and WhatsApp 9696 pages. Apply primary topic rule: if the email's main contribution is about ISQ behavior, it goes in ISQ. If it's about a WhatsApp 9696 feature that uses ISQ, it goes in WhatsApp 9696.
- When merging, update the `source_threads` list in frontmatter to include the new thread ID.
- Check the `related` field — if the existing page already lists a related page, the new content might belong there instead.
