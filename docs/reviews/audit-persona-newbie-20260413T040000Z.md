# Newbie Audit — "Just joined IndiaMart, here's the wiki"

Persona: senior engineer, day one. Timebox: ~45 min. Entry: `wiki/index.md`.
Verification: occasional peeks into `raw/` to sanity-check freshness. Everything
else below comes from the wiki alone.

## 15-minute read: what does IndiaMart do, who matters?

IndiaMART is an Indian B2B marketplace connecting buyers and sellers. The wiki
confirms this indirectly — there's no "About" or landing page, but the domain
shapes jump out fast from the topic list:

- **BuyLead (BL)** is the core monetizable unit — a buyer intent/lead that paid
  sellers consume. Dozens of topics are about BL quality, filtering, pricing,
  approvals, and WhatsApp delivery (`buylead-whatsapp-display`,
  `bl-search-text-match-relaxation-pmcat`,
  `automated-hrs-ticket-creation-on-bl-purchase`,
  `leap-launch-blocking-job-seeker-buyleads`).
- **MCAT / PMCAT** show up as category/mapping taxonomy
  (`mcat-cleaning-via-categorization-auditor`,
  `whatsapp-carousel-phase-3-mcat-city-combination`).
- **PNS, ISQ, CSL, HRS, BMC, RFQ** — the domain lingo is everywhere but is
  never defined on a glossary page. I could infer PNS = Product Not Sold from
  `wiki/systems/whatsapp9696.md` ("Sellers with PNS (Product Not Sold)
  history..."). Most other acronyms I guessed from context.

People who seem important (by link fan-in and leadership-style comments):
- **Dinesh Agarwal** (`da@indiamart.com`) — reviews work, asks sharp questions
  ("That's very little coverage of calibration, Not sufficient"), endorses
  strategy ("Very important to migrate towards this"). Reads like a
  founder/CEO.
- **Amit Agarwal** (`aa@indiamart.com`) — sets policy direction, e.g. the
  Proxylysis page quotes him: "SOARC and other processes should run in
  parallel with development, not as a blocker... Iteration speed must not be
  compromised, but this freedom and pace will not excuse low quality
  development or lack of security." Engineering leadership.
- **Neeraj Agrawal, Mohak Saxena, Pratik Ahuja, Prachi Jain** — recurring
  reviewer/approver names on WhatsApp, BL, and agent topics.

Organisationally: there's a `marketplace-launch` mailing list that acts as the
backbone announcement channel and a `mplaunchim-*` email prefix that maps 1:1
to raw filenames. The wiki is 461 pages: 96 topics, 307 entities, 58 systems,
with `conflicts/`, `policies/`, `timelines/` directories all **empty**.

## Top 3 active projects

1. **WhatsApp9696 Agentic Buyer Chatbot** — LLM-powered buyer chatbot on
   WhatsApp 9696 for repeat buyers, currently at v1.2 on 10% traffic with a
   p95 latency problem (~11s, target <5s) (`wiki/topics/whatsapp9696-agentic-buyer-chatbot.md`).
2. **DSPy + GEPA Automated Speaker Labeling Pipeline** — strategic shift from
   hand-written prompts to data-driven, optimised LLM programs; first prod
   use case is buyer/seller/executive role labelling on transcripts at ~95%
   accuracy (`wiki/topics/dspy-gepa-automated-speaker-labeling.md`).
3. **AI-Powered Gladmin SOA BRD Agent** — in-house tool on the Gladmin admin
   portal that drafts Business Requirement Documents from a ticket ID,
   defaulting to `anthropic/claude-sonnet-4`
   (`wiki/topics/ai-powered-gladmin-soa-brd-agent.md`).

Honourable mentions that also look very active: Proxylysis AI Agent,
LEAP job-seeker BL blocker, Buyer Bot Playground, Intent-based GST Blocker on
BMC, Payment Protection Indicators on BMC.

## Deep-dive: WhatsApp9696 Agentic Buyer Chatbot

**Team.** Spelled out cleanly on the topic page:
- Tech dev: Mukul Singh, Shubham Saxena, Sahil Sharma, Megha Mathur
- Audits/support: Vibha Singh
- Direction: Mohak Saxena, Neeraj Agrawal

**Current state (per wiki).** v1.2 live on 10% of repeat users (GLIDs ending
in 1), planned expansion to 20% "next week". Metrics claimed: 93% intent
classification, 85% resolution, 11s p95.

**Blockers.** Latency is the named blocker ("Improve p95 latency to below 5
seconds"). Management comments include Neeraj's "Looking forward to latency
target achievement by next week".

**Did the wiki deliver?** Partially. The topic page is one of the better
ones — problem, team, version history, metrics, roadmap. But it breaks down
the moment you follow links or ask "is this current?":
- `sources:` lists only 2026-01-02 emails. `raw/` shows **30 emails** on the
  same thread stretching to 2026-02-26. Two full months of decisions,
  deployments, and likely the latency resolution are missing.
- `[[sahil-sharma]]` resolves to `wiki/entities/sahil-sharma.md` which is a
  stub ("Stub page auto-created because [[sahil-sharma]] was referenced but
  no page existed"). The *real* Sahil Sharma lives at `sahil-sharma2.md`
  (`sahil.sharma2@indiamart.com`). Every wikilink to `[[sahil-sharma]]`
  silently points at the wrong page.
- `[[vibha-singh]]` → also a stub.
- The related system link `[[marketplace-launch]]` at the bottom of
  `whatsapp9696.md` isn't even in that page's body — it's in a sibling
  system page.

So: the single page was useful for orientation, but the wiki's promise of
interconnected knowledge falls apart almost immediately.

## 3 frustrations

1. **Stub explosion.** 20 of 307 entity pages and 17 of 58 system pages are
   auto-created stubs with nothing but "Referenced from: [[x]]". Examples
   with real productivity cost: `wiki/entities/sahil-sharma.md`,
   `wiki/entities/m-site.md`, `wiki/entities/vibha-singh.md`,
   `wiki/systems/buylead.md` (!!! the product's core concept is a stub),
   `wiki/systems/leap.md` beyond the bare minimum, `wiki/systems/dir.md`,
   `wiki/systems/gitlab.md`. A good third of wikilink clicks dead-end.

2. **People miscategorised as systems.** `wiki/systems/bolisetty-shravan-kumar.md`,
   `wiki/systems/mohammad-kashif-khan.md`, `wiki/systems/alok-kumar2.md`,
   `wiki/systems/deepak-yadav01.md` — all humans, all filed under systems
   because something referenced them with a slug that a duplicate-detector
   didn't match. There's also `wiki/systems/samarth.md` (stub, "system")
   AND `wiki/entities/samarth.md` (real entity with an email). The
   categorisation is noisy enough that the index is misleading.

3. **Duplicate topic/entity pages and corrupted page tails.** Two topics
   describe the same project with minor title variance:
   `dspy-gepa-automated-speaker-labeling` and
   `dspy-gepa-automated-speaker-labelling-pipeline` — both list themselves in
   the index. Similarly `arjun-gaur.md` and `arjun-gaur-clean.md`,
   `sukanya-sharma.md` and `sukanya-sharma-clean.md`,
   `saurabh-gupta.md` and `saurabh-gupta-clean.md`. And several pages have
   garbled tails that look like a bad merge: `wiki/systems/gladmin.md` ends
   with "`gn compliance checking tool\n- [[marketplace-launch]] - Launch
   mailing list`" (a stutter of the two lines above it), and
   `wiki/topics/leap-launch-blocking-job-seeker-buyleads.md` ends with a
   dangling "`] - Engineering\n- [[mohak-saxena]] - Engineering`". Someone's
   edit tool corrupted them — and `log.md` on 2026-04-13T02:05Z says "18
   broken pages deleted (frontmatter corruption from agent edit_file)", so
   this is a known, ongoing data-quality problem.

## 3 things that worked well

1. **Single-topic technical depth.** `wiki/topics/dspy-gepa-automated-speaker-labeling.md`
   is exemplary: problem framing, before/after approach, the exact accuracy
   table by call-type and duration, production/eval model choice
   (Gemini 2.5 Flash / 2.5 Pro), and explicit "what humans define vs what
   DSPy learns". I could brief a new hire off it in 5 minutes.

2. **`log.md` as an audit trail.** `wiki/log.md` is off-nav but readable
   chronologically and tells you exactly which emails got compiled, which
   pages were created/updated, and which entities had stubs added. On
   2026-04-12T21:12:50Z it lists the LEAP launch, iOS fix, WA Carousel, and
   Buyer Specs compilations — useful for reverse-engineering project status.
   Few wikis bother to keep a machine-legible changelog.

3. **Entity pages as per-person dossiers.** `wiki/entities/dinesh-agarwal.md`
   and `wiki/entities/mohak-saxena.md` preserve short direct quotes with
   dates ("Thanks for initiating this. Very important to migrate towards
   this", "That's very little coverage of calibration, Not sufficient",
   "Brilliant"). The wiki captures *voice*, not just facts — which makes
   figuring out who owns what, who pushes back, and who just forwards
   emails, much faster than a flat org chart.

## Net impression

The wiki is a promising pile — genuinely useful on individual well-compiled
topic pages, and `log.md` is a nice touch — but it is clearly a work in
progress on top of an auto-compilation pipeline that's still fighting
deduplication, categorisation, and data corruption. If I had a week, I'd (a)
run a dedup pass on entity/topic slugs, (b) move mis-categorised humans out
of `systems/`, (c) delete or hide stub-only pages from the index, and (d)
re-compile the WhatsApp9696 Agentic page against the 30 raw emails so the
"current status" actually is current. Right now the index claims 461 pages;
net of stubs and duplicates, closer to ~420 are real.
