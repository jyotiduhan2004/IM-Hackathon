# PM Audit — Status Lookups Against the Wiki

**Auditor persona:** Product Manager doing routine status lookups.
**Entry point:** `wiki/index.md` (461 pages; 96 topics, 307 entities, 58 systems).
**Date:** 2026-04-13

---

## Task 1 — iOS performance fix

**Pages visited (in order):**
1. `wiki/index.md`
2. `wiki/topics/ios-performance-fix-login-flow-v13-6-6.md`

**What I found.** The page is self-contained and actually release-notes-ready:
- Version: **v13.6.6** (in the title and body — "Release Status (April 12, 2026): ~30% of users active on latest release (v13.6.6)").
- Owner / developer: **Lucky Agarwal** ("Developer: [[lucky-agarwal]]", Project ID 651218). Tester: Abhishek Sharma.
- Problem: UI hang during tab-bar initialization post-login, caused by synchronous Realm schema discovery on the main thread (~88% of hang logs).
- Fix: five concrete actions (sync→async `shouldCallService`, `Task{}` wrapping, `async let` parallel DB checks, background `RealmActor` with `ensureReady()`).
- Measurable outcome: **Hang rate 326.1 → 165.7 sec/hour (49% reduction)**, vs Apple p95 target of 1 sec/hour. Expected reduction was 85-90%.
- Known residual: webview logout case — 3% of previous-release hang rate, "under investigation, planned for next release".

**Answerable?** Yes. I could draft the release note from this page alone. The only thing I'd want for polish is an App Store/Play Store release date (I only have "April 12, 2026" as the page's last-compiled timestamp).

**Rating:** ✅ fully answered.

---

## Task 2 — Dynamic Smart RFQ Form

**Pages visited (in order):**
1. `wiki/index.md`
2. `wiki/topics/dynamic-smart-rfq-form.md`

**What I found.**
- **What it is:** a dynamic RFQ form that generates ISQ (Information Seeking Questions) tailored to what the buyer searched for. Launched on **BuyerMY for a limited set of MCATs**.
- **Current status:** live in a limited rollout; two named quality issues (redundant ISQ like asking "Preferred Brand" when the search is "HP Desktop computers"; overly-specific ISQ like "Telescopic Handle" for "Magic Mop"). Concrete OFR IDs are cited (139658893213, 139632695046).
- **Version:** "Version 2 launch planned to address redundant ISQ issues." Implicitly V1 is live. No Version 2 date.
- **V2 design requirement:** "[[dinesh-agarwal]] requested to avoid dropdowns and use open selection options instead."
- **Potential integration:** WhatsApp9696 is being discussed for info collection.

**What's missing / frustrating.**
- **No clear owner/driver.** The related list has Neeraj Agrawal (asked questions), Tarbrinder Singh (context unclear), Dinesh Agarwal (requested a design constraint). No "Owner:" or "PM:" field. Neeraj reads as a stakeholder asking for updates, not the DRI.
- **No V2 timeline or scope list** — just "planned".
- **No current metrics** ("Metrics tracking for sustained period" is aspirational).

**Rating:** ⚠️ partially answered — I know what it is and roughly where it stands, but I cannot tell you who is driving it or when V2 ships.

---

## Task 3 — WhatsApp integration(s)

**Pages visited (in order):**
1. `wiki/index.md`
2. `wiki/systems/whatsapp.md` (thin wrapper — "referenced across multiple IndiaMART features")
3. `wiki/systems/whatsapp9696.md` (the substantive system page)
4. `wiki/topics/whatsapp9696-agentic-buyer-chatbot.md`
5. `wiki/topics/whatsapp-messaging-enhancement.md`
6. Directory scan of `wiki/topics/` for WhatsApp-related pages

**What I found.** There isn't "one" WhatsApp integration — there's a platform (`whatsapp`) and a primary product (`whatsapp9696`) with at least **10 in-flight topic pages** touching it:

| Topic | State |
|---|---|
| whatsapp9696-agentic-buyer-chatbot | v1.2 live on 10% repeat users (GLIDs ending in '1'); 93% intent accuracy, 85% resolution, p95 latency 11s vs target <5s; planned 20% expansion |
| whatsapp-smarter-seller-recommendations | Live; 5/6 functional + 30/30 smoke passed; known bug ticket 655191 |
| whatsapp-messaging-enhancement | Enhancement rolled out (search keyword in message body); bug summary HP:0/MP:58/LP:5; Neeraj waiting on screenshots |
| whatsapp-carousel-phase-3-mcat-city-combination | current |
| whatsapp-nudge-timing-optimization-test | A/B test, current |
| post-call-whatsapp-feedback-pns | current |
| buyer-feedback-on-bl-purchase-seller-introductions-via-whatsapp | current |
| buylead-whatsapp-display | current |
| astbuy-product-images-whatsapp | current |
| complaint-agent-v2-whatsapp-9696 | current |

The system page `whatsapp9696.md` additionally covers the Context-Aware Pricing Framework (7/7 functional, 36/36 smoke, 100% pass rate) and a Buyer CSAT feedback collector live on 10% repeat-buyer traffic.

**What's missing / frustrating.**
- There's no single landing page that lists "all WhatsApp workstreams in flight" — I had to discover the 10 topics by scanning filenames. The `systems/whatsapp.md` page is a 20-line stub that could be the hub but isn't.
- Many rollout percentages and known bugs are listed but **without owners or target dates on the system page** — I have to click into each topic.

**Rating:** ✅ fully answered on "is there one and what are they" — ⚠️ on navigation (had to hunt).

---

## Task 4 — Random topic page

**Page chosen:** `wiki/topics/trustpulse.md` (picked by name alone — I had no idea what "TrustPulse" was).

**Can I explain it in two minutes?** Yes, comfortably:
- TrustPulse is an **internal procurement CRM for TrustSEAL Buyer subscribers**, built by the BuyerMY team.
- Built "in a weekend, refined in a week, zero internal engineering effort" — on Bolt.new, backed by Anthropic Claude + MongoDB Atlas. Live.
- Replaces the manual Excel/WhatsApp/phone-log workflow TrustSEAL Buyer ops were running on. Positioned as either a Buyer-WebERP replacement or inspiration for a future BI-team-built version.
- Roadmap is P1/P2/P3 tagged: 9696 WhatsApp barge-in, renewal marketing, Redshift/GA sync (P1); automated follow-ups (P2); AI procurement agent (P3).
- Budget: Amarinder Dhaliwal approved prototype-phase use of existing Bolt/Claude credits; no separate approval needed yet.

Clean page, concrete enough to brief a colleague.

**Rating:** ✅ fully answered.

---

## Task 5 — Three random entity pages

**Pages visited (in order):**
1. `wiki/entities/chittresh-lohani.md`
2. `wiki/entities/nadeem-suhaib.md`
3. `wiki/entities/ramu-lath.md`

I deliberately picked names I didn't recognize. Results were very uneven.

**Chittresh Lohani** — ✅ genuinely about him.
- Role stated explicitly: "Implementation Lead".
- Three active workstreams listed with context: Context-Aware Pricing Framework for WhatsApp9696 (primary), Live Assistant Buyer Helpdesk (CC'd), Buyer Bot Playground (recipient). I can tell he *owns* the pricing framework.

**Nadeem Suhaib** — ⚠️ partially about him.
- Team stated: Photo Search Team. Email included.
- Three specific contributions: Qdrant pipeline test/validation, Photosearch star-rating 50% rollout analysis, general Photosearch work. But notes like "tasked with conducting quick impact analysis by Monday" read more like logged tasks than a persistent role description.
- "Focus Areas" bullets (A/B test impact analysis, user engagement, scaling) feel auto-extracted rather than authored.
- `sources: []` and `related: []` in frontmatter despite having 5 related links in the body — frontmatter looks incomplete.

**Ramu Lath** — ❌ essentially a stub.
- Just an email (`ramu@indiamart.com`) and two `[[links]]` to topics he appears in (Product Title Biztype Rejection, Auditor 1.1 rollout — the latter annotated only as "Recipient of ... rollout announcement").
- No role, no team, no ongoing work. I cannot tell whether this is a developer, an auditor, a CEO, or the janitor. The page is **about emails he touched**, not about *him*.

**Takeaway.** Entity-page quality is highly variable. Pages like Chittresh's are actually useful "who owns what" references. Pages like Ramu's are filename-in-an-index-card — they pollute the entity list (307 entries) and make "pick a person, learn about them" unreliable.

**Rating:** ⚠️ partially — system *can* answer this well but doesn't consistently.

---

## Overall observations

1. **The topic pages are the crown jewels** — iOS, TrustPulse, Smart RFQ, the WhatsApp topics all read like PM-grade status updates with metrics, owners (sometimes), and next steps.
2. **System pages are inconsistent.** `systems/whatsapp9696.md` is rich; `systems/whatsapp.md` is a 20-line stub. A PM scanning `systems/` first would not discover the real activity.
3. **No owner/status fields in frontmatter.** Every topic has `status: current` but no `owner:`, `stage:` (alpha/beta/GA), or `target_date:`. Discovery relies on scanning prose. This is the single biggest gap for a PM audit.
4. **Entity pages are 2-tier** — some are authored bios, many are "this person appeared in N emails" auto-generated pages. Filtering or marking the stubs would make the entity index actually navigable.
5. **No cross-cut views.** There's no "iOS releases", "all WhatsApp work", or "BuyerMY roadmap" rollup — a PM has to reconstruct these by filename scan. `systems/whatsapp.md` and `systems/buyermy.md` could serve as hubs but aren't wired that way.

If I were publishing a release-notes draft for a single shipped item (Task 1), I could do it. If I were asked "what's the state of WhatsApp across the org?" I could build the list but would need to click into ten topics to answer it. If I were asked "who is Ramu Lath?" I'd still be guessing.
