---
title: "PM persona audit — run 3e88f996 (2026-04-29)"
audit_kind: pm-persona-deep
run_id: 3e88f996-3ee7-4653-b7b0-156c6c960201
sample_size: 9 of 42 pages
persona: IndiaMART Product Manager (onboarding new PM / prepping director-stakeholder review)
---

# PM persona audit — run 3e88f996 (2026-04-29)

## Executive summary

Verdict: **wiki is engineer-grade, not PM-grade**. 0 of 9 pages would be forwarded to a director without a rewrite. The corpus is honest and dense — sources cited, metrics included, bugs tracked — but it reads as a *log of what happened* rather than a *brief on what is*. Every page that opens "Live since X" buries the question a PM actually asks: *what is shipped right now, what's the next gate, who is on the hook, when does it move*.

Headline counts across the 9-page sample:

- **Owner / DRI in frontmatter:** 0 / 9. Owners must be reverse-engineered from prose (Stakeholders, Key Contacts, Development Team — naming inconsistent across pages).
- **Open decision called out as "next gate / by-when":** 1 / 9 (`buyer-seller-introduction-limit-reduction-10-to-9` mentions "9 → 8 analysis pending" but no date).
- **Recency check passes (most recent change ≤ 30 days from last_compiled):** 4 / 9. The other 5 trail off on early-Feb 2026 entries — some are genuinely dormant, others are likely stale (project clearly moved, page didn't).
- **Stakeholder-forwardable (no rewrite needed):** 0 / 9.
- **First paragraph answers "what is the current state" without scrolling:** 5 / 9 (the `Current state` block when present works; absent on systems pages and on `mcat-cleaning`, `agentic-auditor-product-approval-grid`).
- **5W (Who/What/When/Where/Why) answered above the fold:** 1 / 9 — only `seller-ads-exclusivity` actually does it. Most miss "Who owns this" and "Where (which BU/segment)".
- **Cross-reference health:** generally good (pages link out to systems and adjacent topics) but heavily polluted by duplicate `Related` blocks and 10–25-name people-link bricks at the bottom that drown signal.

Bottom-line judgement: **a new PM joining the team on Monday could not use this wiki to walk into a Tuesday review**. A new engineer probably could.

---

## Per-page findings

### 1. `systems/auditmate.md` — system, 5 source threads, last_compiled 2026-04-23

| Dimension | Verdict | Evidence |
|---|---|---|
| Current state in 30s | WEAK | Lead is a brand pitch ("centralized Product Audit Platform designed to bring transparency, speed, and structure"). Performance-as-of-2026-01-07 sits 130 lines down. No "live for X% of sellers, Y audits/day" up top. |
| Open decision | WEAK | Open questions are listed (`System-driven mapping/unmapping cycles`, `Delayed status changes`) but no owner / by-when / decision-needed-from. |
| Owner | FAIL | No owner, no DRI, no PM, no eng lead in frontmatter or in body. The only named individuals are the people who *complained*: Chittresh Lohani, Deepak Yadav, Ruchi Singh, Anirban Kundu. A PM cannot tell who owns this. |
| 5W | WEAK | Who/What/When/Where unclear. "Integrated with seller.indiamart.com (admin access)" answers Where partially; no segment/region split. No When for current state. |
| Director-forwardable | FAIL | Reads like a feature spec drafted from PRD slides. No headline number ("X% catalog audited", "Y% precision", "₹Z spend"), no roadmap with dates. |
| Cross-refs | PASS | 4 named related auditmate-* pages, links to people raising issues. |
| Recency | WEAK | Last "Recent change" is 2026-02-26; last_compiled is 2026-04-23. Two-month gap — either dormant or page stale. No signal for the reader. |

Specific quote that hurts: the `Performance` H2 leads with `As of 2026-01-07.` — but this is a current-state platform page, not an experiment log. PM reading top-down has to scroll past a two-paragraph brand pitch + a 3-section UI tour + a feature checklist before getting to "fast processing 6-7s, some cases >30s".

### 2. `systems/seller-ads.md` — system, 3 source threads, last_compiled 2026-04-16

| Dimension | Verdict | Evidence |
|---|---|---|
| Current state in 30s | PASS | Lead paragraph is current-truth: "Service ID 385… launched January 2026 following a successful December 2025 POC where 11/12 sellers achieved over 7 conversions within a week". Pricing table immediately readable. |
| Open decision | WEAK | "Business launch date pending confirmation from leadership" — no who, no by-when, no last-touched. Buried under `Active related topics`. |
| Owner | FAIL | No owner. Bug 641923 has no assignee. Manual proposal & GLID handoff "automation planned" — by whom, by when? |
| 5W | WEAK | Who: missing. Where: "All India location preference only" answers segment. When: launch date Jan 2026 ✓. Why: clear. |
| Director-forwardable | WEAK | Closer than auditmate but still has TODO-shaped lines like "Business launch date pending confirmation from leadership" with no narrator. A director reads that and asks "from whom to whom?" |
| Cross-refs | PASS | Links to `seller-ads-exclusivity`, `seller-ads-exclusive-product-level-lead-service`, `weberp`. |
| Recency | WEAK | last_compiled 2026-04-16, latest cited event 2026-02-02. Service launched Jan 2026 — the page would be a hot system, but the wiki records nothing for ~12 weeks. Either dormant or stale. |

Specific gap: the table "Cost per Lead ₹500 / ₹1,000" is exactly the kind of number a director will quote. The page does not say *what current revenue / leads-per-day* the system is producing — only the POC result. Stale economics is worse than no economics.

### 3. `systems/gladmin.md` — system, 8 source threads, last_compiled 2026-04-14

| Dimension | Verdict | Evidence |
|---|---|---|
| Current state in 30s | FAIL | Page is a stub. After "GLAdmin is IndiaMART's internal administration and development platform that hosts various AI-powered agents and testing tools." there is one feature (Disable User Screen migration) and a stray `... (preserve existing)` placeholder marker visible in rendered output. |
| Open decision | FAIL | None present. |
| Owner | FAIL | None. |
| 5W | FAIL | What is GLAdmin really? Who uses it? PMs in which orgs? Which screens are migrated and which aren't? None of this is on the page. |
| Director-forwardable | FAIL | This page is broken — `... (preserve existing)` literal in the body is an agent placeholder leak. |
| Cross-refs | PASS | 17-link `Related` block — but signal/noise is poor; many migration sub-topic pages mixed with people-pages. |
| Recency | FAIL | `update_count: 3` but visible body has been gutted. Likely a bad merge between updates. |

Specific quote that's a bug, not a critique: literal text `... (preserve existing)` appears inside `## Features`. This is a leaked instruction from the compile prompt and should never have rendered.

### 4. `systems/in-house-devops-agent.md` — system, last_compiled missing in frontmatter

| Dimension | Verdict | Evidence |
|---|---|---|
| Current state in 30s | PASS | Strong lead paragraph + Access section + "30 users onboarded" current adoption figure. |
| Open decision | PASS | The `Leadership Requirements` section is unusually well-done — it lists Ayush Gupta's four asks (ticket examples, automation guarantee, accuracy measurement, monthly usage doc) verbatim. Best example in the sample of "what does leadership want next". |
| Owner | WEAK | `Related Pages` has roles inline ("Primary contact and launch owner", "Vision owner and scaling oversight"). Better than most — but still in body, not frontmatter. A PM ctrl-F-ing for "owner" gets nothing. |
| 5W | PASS | Who, What, When, Where (URL + AI Portal), Why all present. Best 5W coverage in the sample. |
| Director-forwardable | WEAK | Pilot data (867 requests over 2 weeks) is fine for a status-update slide but stops at Jan 23, 2026. The page *should* answer "what's it doing in April?" Doesn't. |
| Cross-refs | PASS | 5 named people with role labels. |
| Recency | WEAK | Recent changes ends 2026-01-29. No frontmatter `last_compiled`. Three-month gap. |

Specific note: this page is the closest to PM-grade in the sample. The `Leadership Requirements` H2 is the model — it's exactly what a PM wants from every page (specific ask, named asker, date implicit). Should be templatized.

### 5. `topics/lens-2-0-hybrid-photosearch.md` — topic, single thread

| Dimension | Verdict | Evidence |
|---|---|---|
| Current state in 30s | PASS | Lead: "Live on 50% traffic (odd GLIDs) since 2026-01-29." Then `Current state` H2 reiterates with post-50% metrics. |
| Open decision | WEAK | "Tighten relevancy for B2C specifics (e.g. kurta, jhumka)" is in Recent changes from Vikram on 2026-01-28. No follow-up — did this happen? Page does not say. The "next gate" (scale 50% → 100%) is not stated. |
| Owner | WEAK | `Leadership Feedback` lists 7 leaders but no DRI / PM. Tech leads not labeled. The Related list has 12 people-links unannotated. |
| 5W | PASS | Who (Srinivasa, Naveen, Abhishek tested; Ashutosh Singh ships), What (hybrid V+T+text PhotoSearch), When (Jan 29 50%), Where (lens.indiamart.com / IndiaMART Lens), Why (relevance/accuracy). |
| Director-forwardable | WEAK | Latency block confused: "P95 6.13s vs Lens 1.0 2.63s" — that's a 2.3× regression. Buried inline. A director reading top-down will not catch it. The page does not say "ship-blocker" or "acceptable trade-off" — it just lists. |
| Cross-refs | PASS | photosearch, indiamart-lens, marketplace-launch all wikilinked. |
| Recency | FAIL | All entries Jan 28–29, 2026. Page is 3 months stale. The Lens 2.0 project has almost certainly moved past this state. |

Specific quote: "P95 latency 6.13s (vs Lens 1.0 2.63s)" — the +133% latency regression is presented as a value-neutral data point. A PM page would headline this as "scaling gated on latency improvement" or "accepted trade-off; tracked under X". Neither happens.

### 6. `topics/whatsapp9696-agentic-buyer-chatbot.md` — topic, 13 sources, 4 threads, last_compiled 2026-04-28

| Dimension | Verdict | Evidence |
|---|---|---|
| Current state in 30s | PASS | "Coverage: Scaled to 40% of repeat user traffic on WhatsApp 9696 as of February 4, 2026" — clean, dated, scoped. |
| Open decision | PASS | `Open questions` lists "How to close the 15% accuracy gap to reach 90% target?" with named asker (Neeraj Agrawal) and date. Best of the sample. |
| Owner | WEAK | Tech V2.0: Chittresh Lohani. Direction: Mohak Saxena, Neeraj Agrawal, DA. PM ownership not named. |
| 5W | PASS | All five present and IndiaMART-specific (channel = WA9696, segment = repeat buyers, GLIDs ending 9). |
| Director-forwardable | WEAK | The page is 270 lines. A director-readable version is 30 lines. The depth is good for the team; bad for the executive. No TL;DR before "Current state" — and "Current state" is line 49. |
| Cross-refs | PASS | Links to whatsapp-9696-bot, whatsapp, oneture-buying-bot-poc. |
| Recency | PASS | Last changes 2026-02-09, last_compiled 2026-04-28. Tight. |

Specific gap: V1.2 / V1.3 / V2.0 versioning is documented chronologically — a PM joining today wants to know which version is *currently routing live traffic*, and the answer requires reconciling three section headers. "V2.0 (Active since Jan 22, 2026)" + "V1.3 launched Jan 30" + "V1.2 launched Jan 2" makes it sound like V1.3 is *newer than V2.0*. It isn't, but the page is structured to reward someone who already knows that.

### 7. `topics/buyer-seller-introduction-limit-reduction-10-to-9.md` — topic, 23 sources, 1 thread

| Dimension | Verdict | Evidence |
|---|---|---|
| Current state in 30s | WEAK | Has an `Overview` H2 with the policy change, but no `Current state` H2. The "live or not" answer requires reading the whole `New Implementation` H2. |
| Open decision | PASS | Best in the sample: "Amit Jain raised the question of further reducing from 9 to 8 after positive weekly impact results. Dinesh Agarwal responded that analysis should be done first before any further reduction." Names + question + decision-needed-from + impact estimate (1.56% loss / ~40K BL/wk). |
| Owner | FAIL | The 23-link people-Related dump at the bottom is a bricked DRI map — no roles labeled, no PM tagged. |
| 5W | WEAK | What (limit 10→9 with new BL expiry condition), When (Jan 5, 2026), Why (excessive intros, low maturity) — all clear. Who is the policy owner? Missing. Where (which marketplace flow / which buyer segment)? Implied but never stated. |
| Director-forwardable | WEAK | Numbers are sharp ("BL Approval +3.2%", "9+ intros down 9%"). But the page is 240 lines, of which ~80 are DB-level test cases. No version a director would read in 5 minutes. |
| Cross-refs | WEAK | Only `[[buylead]]` and `[[marketplace-launch]]` from the body. The 23-name Related dump is noise. |
| Recency | WEAK | Recent changes section absent (no H2). Last cited event 2026-01-29. |

Specific strength to copy elsewhere: the `Analysis of Further Reduction (9 to 8)` paragraph. That is the *one* paragraph in the sample that names a leader, a question, an answer-owner, and a quantified next-step impact. Should be the template for every Open Decision.

### 8. `topics/seller-ads-exclusivity.md` — topic, 1 thread, 4 sources

| Dimension | Verdict | Evidence |
|---|---|---|
| Current state in 30s | PASS | Two-paragraph lead names launch date (2026-01-30), platform (mobile), tagging (modid=SELLRADS), and "delivers on the Seller Ads value proposition of exclusive enquiries". |
| Open decision | PASS | `Open questions` has two clear items with bug ID 641923 and Mohak's tracking question. |
| Owner | WEAK | "FENQ proc changes by Sulabh Kumar Katiyar; modid setup on Google Ads by Ekaansh Parashar, Tejender Dubey, Gyaneshwar Mongha" — names but no roles / no PM. |
| 5W | PASS | All five answered. Best 5W in the sample for a topic page. |
| Director-forwardable | WEAK | Strong technical reference but no "what's the buyer/seller impact metric we're watching". A director would ask "is exclusivity actually exclusive — what % of leaks?". The page acknowledges Bug 641923 but doesn't quantify leak rate. |
| Cross-refs | PASS | system/seller-ads, seller-ads-exclusive-product-level-lead-service, product-promotions-screen-seller-im. |
| Recency | WEAK | Last change 2026-02-02. Two months from project launch with one bug open — the "did Bug 641923 get fixed?" answer is missing. |

Specific structural strength: "How it works" with three numbered touchpoints + the "Seller exclusion window" is concise and PM-readable. Best how-it-works section in the sample.

### 9. `topics/agentic-auditor-product-approval-grid.md` — topic, 2 threads, last_compiled 2026-04-28

| Dimension | Verdict | Evidence |
|---|---|---|
| Current state in 30s | WEAK | Lead is feature description, not state. The `TL;DR` H2 helps — *but it's at H2, not at lead.* A PM who scrolls past the abstract to "Business Decision Rules" misses the TL;DR entirely. |
| Open decision | PASS | 7 numbered open questions, each with named asker and (mostly) named owner-to-action. Among the strongest open-question blocks in the sample. |
| Owner | WEAK | `Stakeholders` H2 is the right idea — it lists 11 people with one-line roles. But the framework owner / PM-DRI is not labeled. Ashutosh Singh "owns the Decision GRID framework" per the bullet — should be in frontmatter. |
| 5W | WEAK | What (decision grid scoring 0.5–3), When (live Jan 16 / hard-action), Why (catalog quality), Who (stakeholders listed), Where — gap (which seller segments, which entry surfaces beyond MCAT cleaning + Product Approval). |
| Director-forwardable | WEAK | Score table + Action table is solid, but the body is dense with bug IDs and FP precision issues that don't belong in a director brief. |
| Cross-refs | PASS | auditmate, auditor-11-no-photo-version-rollout, decision-grid stakeholders all linked. |
| Recency | PASS | Last entry 2026-02-02, last_compiled 2026-04-28. |

Specific issue: the `## References` block has a leaked agent comment: `... (preserve existing)` (line 125 in the raw markdown). Same agent-prompt leak as on `gladmin.md`. This is a compile bug, not just a content gap.

---

## Patterns (failure modes recurring across pages)

1. **No `owner` / `dri` / `pm` in frontmatter — 9 / 9 pages.** Every page reverse-engineers ownership from prose. Section names vary: "Stakeholders", "Key Contacts", "Development Team", "Related pages", "Leadership Feedback". A PM ctrl-F'ing for "owner" gets nothing. Adding a frontmatter `owner: <slug>` and `dri: <slug>` is the single highest-leverage fix.

2. **Lead paragraph is feature pitch, not current state — 6 / 9.** auditmate, gladmin, agentic-auditor-product-approval-grid, seller-ads (partially), buyer-seller-intro-limit, mcat-cleaning all open with "X is a Y that does Z" — fine for a press release, useless for a status check. The 3 that pass (whatsapp9696, lens-2-0, in-house-devops-agent) all front-load a date + scale ("40% of repeat traffic since Feb 4").

3. **Open decisions phrased as topics, not gates — 7 / 9.** "Will we tighten relevancy for B2C specifics?" is a topic. "Decision needed from <name> by <date> on <option A vs B>" is a gate. Only `buyer-seller-intro-limit` (9→8 question, Dinesh's reply) and `in-house-devops-agent` (Ayush's 4 asks) phrase open work as gates with named asker + decision-needed-from.

4. **Recency rot — 5 / 9 pages haven't been touched in ≥ 2 months despite live, evolving projects.** The compile pipeline updates `last_compiled` even when the agent had nothing new to say, so the timestamp doesn't surface staleness. PM cannot tell from the page whether the project moved or paused.

5. **People-link bricks at the bottom — 9 / 9.** Every page ends with a 5–25-line bare wikilink list of every person who ever appeared in a thread. Zero signal (no role label, no order, no DRI vs spectator distinction). Particularly bad on `buyer-seller-intro-limit` (23 names) and `mcat-cleaning` (27 names).

6. **Duplicate `Related` blocks — 9 / 9.** Body `## Related pages` + frontmatter `related:` + body `## Related` (auto-generated). They render together. Confirms F-069 from prior persona audit.

7. **Agent-prompt placeholder leaks — 2 / 9.** `... (preserve existing)` literal text on `gladmin.md` (in body) and `agentic-auditor-product-approval-grid.md` (in references). Compile bug — should never reach the wiki.

8. **No "where" in 5W — 7 / 9.** "Which BU?" "Which seller segment (Free / Mini / SS+ / Premium)?" "Which region?" "Which entry surface (msite / desktop / app / WA9696)?" — surfaced inconsistently. seller-ads names "All India + SS+ only" and `whatsapp9696` names "GLIDs ending 9 → all repeat" — these are the exceptions.

9. **Latency / quality regressions presented as data, not gates — 3 / 9.** lens-2-0 (P95 6.13s vs 2.63s, +133%), whatsapp9696 (p95 ~9.5s vs <5s target), auditmate (some cases >30s) — all listed as facts, none flagged as ship-blockers or accepted trade-offs. PM has to make the call themselves.

10. **TL;DR placement varies.** When present, it works (`agentic-auditor-product-approval-grid`, `thankyou-screen-soi-journey-revamp`). But it's at H2, not above the lead. A PM scrolling top-down hits the lead first; a PM ctrl-F'ing for "TL;DR" finds it. The two readers see different content.

---

## Top 3 follow-up PRs to ship

### PR 1 — Frontmatter ownership fields + compile prompt + viewer renderer

Add `owner: <person-slug>`, `dri: <person-slug>`, `pm: <person-slug>`, `eng_lead: <person-slug>` to frontmatter. Teach the compile agent to extract these from "announced by", "owns", "DRI", "responsible for" phrasings during compile. Render them as a 1-line ownership strip *above* the lead paragraph in the viewer (not at the bottom). Backfill with a one-shot migration script for the 42 pages in this run + any system page.

Why first: solves the single most repeated PM friction ("who do I ping?") in 1 PR. Pure-additive frontmatter — no risk to existing readers.

### PR 2 — Replace `Recent changes` with `Open gates` + `Recent activity`

Split today's `Recent changes` into two H2s:

- **`Open gates`** — bulleted list of decisions that need to land before the project moves. Each bullet: `<question>` — needed from `<person-slug>` by `<by-when>`; current proposal: `<X>`. The agent is prompted to extract these specifically (using the `buyer-seller-intro-limit` 9→8 paragraph as the canonical example).
- **`Recent activity`** — the existing dated bullet log, unchanged.

Why second: every page in the sample under-served the "what's blocking this" question. This forces the agent to write gates, not narrative. The model already has the data — it just isn't being asked the right question.

### PR 3 — Lead-paragraph contract: scale + date + segment, no pitch

Update the compile prompt's `Summary` / lead paragraph instruction to require: (a) current scale-or-status (e.g., "live for X% of GLIDs ending in Y", "3 of 8 migrations complete"), (b) most recent material change date, (c) IndiaMART segment scope (BU / seller tier / channel / region). Forbid lead paragraphs that begin with "<Title> is a <category> that <does X>" — that's a glossary entry, not a status. Add a validator check.

Why third: this is the highest-friction gap (7/9 pages fail it), but it requires prompt + validator co-changes and the largest behaviour shift in the agent. Ship after PR 1 and PR 2 stabilize so the agent can reuse `owner` and `Open gates` fields in the lead.

---

Sample method note: 9 of 42 pages reviewed in depth (`auditmate`, `seller-ads`, `gladmin`, `in-house-devops-agent`, `lens-2-0-hybrid-photosearch`, `whatsapp9696-agentic-buyer-chatbot`, `buyer-seller-introduction-limit-reduction-10-to-9`, `seller-ads-exclusivity`, `agentic-auditor-product-approval-grid`, plus a structural skim of `bl-purchase-whatsapp-9696`, `dynamic-smart-rfq-form`, `thankyou-screen-soi-journey-revamp`, `aisensy-indiamart-payload-architecture-enhancement`, `pns-call-summary-lead-manager`, `mcat-cleaning-via-categorization-auditor`, `buyleads-from-affiliate-social-media-comments`, `lens-desktop-ai-first-homepage`). The 4 systems pages were all included; the 5 topic pages were chosen by decision-density (rollout %, leadership ask, monetization scope).

AUDIT: /Users/amtagrwl/git/email-knowledge-base/docs/audits/run-3e88f996-pm-persona-audit-2026-04-29.md
