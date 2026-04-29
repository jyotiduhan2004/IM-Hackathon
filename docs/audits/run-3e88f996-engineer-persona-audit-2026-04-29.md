---
title: "Engineer-persona deep audit — run 3e88f996, 2026-04-29"
audit_kind: persona-deep
persona: "New IndiaMART engineer, week 2 (read the wiki to ramp)"
run_id: 3e88f996-3ee7-4653-b7b0-156c6c960201
post_pr: pre-251 / pre-252 / pre-253
sample: 9 of 42 pages compiled in run (4 system + 38 topic)
---

# Engineer-persona deep audit — run 3e88f996

## Executive summary

**No.** A new engineer in week 2 cannot become effective from these pages alone. The wiki is fluent in **launch-PR vocabulary** ("scaled to 100%", "QA found 5 defects", "ticket 639598") but largely silent on the things that determine whether a fresh engineer can actually do work: the request shape that hits a service, the repo that owns the code, the alert that pages on call, the dashboard that proves the service is healthy, the upstream/downstream graph the change rides on. The most common technical artifact across 9 sampled pages is **a Kibana URL with a hash slug** (`https://imkibanaindia.intermesh.net/app/r/s/ePE6G`) — opaque to anyone who hasn't already been onboarded to the dashboard taxonomy. A new engineer reading `gladmin-administrator-read-apis-gin-migration` knows that nine Postgres tables exist, but cannot tell you which repo holds the migration, which service exposes the routes, which Gin-version was adopted, where the LOG_ID is emitted, or what the `c.Bind` validation contract actually looks like. The standout: `msite-mcat-gke-migration` — quantitative before/after table, sample URLs, ticket IDs, multiple test devices. Even there, **zero** mentions of the GCP project, namespace, HPA limits, image registry, or rollback runbook. **The wiki is currently a launch-announcement digest, not a system reference.** The glossary is actively misleading (`AI = GenUI Agent`, `API = critical seller-facing service`) — a week-2 engineer using it would learn wrong. Onboarding requires Slack DMs and tribal knowledge. That is the gap to close.

## Pages sampled (9 — biased toward technical surface)

| # | Slug | Type | Domain |
|---|---|---|---|
| E1 | `systems/in-house-devops-agent` | system | engineering-productivity |
| E2 | `systems/gladmin` | system | (no domain field) |
| E3 | `systems/seller-ads` | system | growth-monetization |
| E4 | `topics/gladmin-administrator-read-apis-gin-migration` | topic | platform-reliability |
| E5 | `topics/gladmin-administrator-write-apis-gin-migration` | topic | engineering-productivity |
| E6 | `topics/msite-mcat-gke-migration` | topic | marketplace-discovery |
| E7 | `topics/lms-replytowhatsapp-consumer-migration` | topic | platform-reliability |
| E8 | `topics/aisensy-indiamart-payload-architecture-enhancement` | topic | ai-automation |
| E9 | `topics/centralized-runtime-app-permission-tracking` + `topics/deeplink-parsing-logic-improvement` | topic pair | seller-experience |

(Bonus cross-reference: `topics/real-time-d-rank-dadb-removal-wrong-category-ni`, `topics/whatsapp9696-agentic-buyer-chatbot`, `topics/im-insta-pro-unread-message-handling`, `topics/auditmate-sellerim-integration` — used to triangulate patterns.)

## Dimension legend

| Dim | Meaning |
|---|---|
| NAV | Can I get from this page to the services/repos/dashboards I'd touch? |
| ID | Concrete identifiers (endpoints, tables, env vars, GCP/AWS resource names, alert names, repo paths) visible? |
| FAIL | Failure modes / who pages / where to look first? |
| DIAG | Diagrams, schemas, payloads, IDL, mermaid? |
| XREF | Cross-domain references — what other pages should I read? |
| ACR | Acronym hygiene — IndiaMART terms expanded or glossary-linked on first use? |
| CODE | Code/config snippets, accurate and unmangled? |

## Per-page findings

### E1 — `systems/in-house-devops-agent`

| NAV | ID | FAIL | DIAG | XREF | ACR | CODE |
|---|---|---|---|---|---|---|
| WEAK | WEAK | FAIL | FAIL | WEAK | WEAK | FAIL |

> "Platform: Google ADK (Agent Development Kit) — LLM Gateway: imllm.intermesh.net — Grafana MCP: Connects directly to monitoring stack — Kubectl MCP (kubectl-ai): Safe interaction with Kubernetes clusters"

This is **the entire architecture section** for a system that an on-call engineer might be debugging at 3 AM. There is no:

- repo path for the Google-ADK agent code
- name of the Service Account or its role bindings ("strictly read-only" is a claim, not a contract)
- list of which K8s clusters and namespaces are in scope (prod-marketplace? prod-seller? all?)
- alert name or PagerDuty/Opsgenie escalation path if `dev-assist.intermesh.net` itself goes down
- API contract for "Grafana MCP" — what tool calls? what auth?
- onboarding URL for adding new tools to the agent (just "contact `[[soa-devops]]`")

**ACR**: ADK is expanded once, never linked to glossary. **MCP** is used twice and never expanded — a week-2 engineer might guess Mission Control Panel, Multi-Cloud Proxy, Model Context Protocol; correct answer (MCP = Model Context Protocol) is nowhere on the page.

**Verdict for a new engineer**: I can find the URL to *use* the assistant. I cannot find the code, the repo, or who pages when it breaks.

### E2 — `systems/gladmin`

| NAV | ID | FAIL | DIAG | XREF | ACR | CODE |
|---|---|---|---|---|---|---|
| WEAK | WEAK | FAIL | FAIL | PASS | FAIL | FAIL |

> "GLAdmin is IndiaMART's internal administration and development platform that hosts various AI-powered agents and testing tools."

That is line 37, the entire definition. After that the page becomes a list of feature wikilinks. There is **no answer to**:

- What stack is GLAdmin? (PHP? React? Node? Some answer, since the body mentions "Migrated from PHP to React-NodeJS using GenUI Agent" — but in passing.)
- Which repo? Which CI/CD?
- What does the homepage URL actually serve — a router, a portal, a SPA?
- Who owns the platform vs. the individual screens?

**The "GLAdmin SOA Migrations" section appears twice** — once with placeholder `... (preserve existing)` (line 45) and once for real (line 49). This is a clear bug from a sloppy merge — would shake any engineer's trust on first visit.

**ACR**: SOA used 4 times, never expanded. (Service-Oriented Architecture, presumably, but a week-2 hire with a Java background might assume something else.) **FCP** referenced ("FCP Read APIs", "FCP Write APIs") and never expanded **anywhere on the page** — I could not even guess. (First-Call-Party? Functional-Component-Provider? Frontend-Console-Process?)

**Frontmatter**: `domain` field missing entirely. By contrast E5 says `domain: engineering-productivity` and E4 says `domain: platform-reliability`, so even sister Gin-migration pages have inconsistent domains.

### E3 — `systems/seller-ads`

| NAV | ID | FAIL | DIAG | XREF | ACR | CODE |
|---|---|---|---|---|---|---|
| WEAK | PARTIAL | WEAK | FAIL | PASS | PARTIAL | FAIL |

The strongest "system" page in the sample for a *PM*. For an *engineer* it is still thin:

- "Service ID 385" is named — the **only concrete service ID anywhere in the 9 pages**. Excellent. Yet no link to whatever registry "service IDs" live in.
- "modid=SELLRADS" appears as a query-param contract — usable.
- "Bug 641923 — MODID not correctly set to SELLRADS for intent-based BLs" — actionable.
- BUT: no API endpoint, no DB table for the "product promotion dashboard", no campaign/lead-routing schema, no integration boundary diagram between Seller Ads and Google Ads / Merchant Centre.
- "via [[seller-ads-exclusivity]]" wikilink hides the actual exclusivity rule. A new engineer has to two-hop to learn what "exclusive" means in code.

**ACR**: SS+ used and never expanded. POC fine. BL fine (in glossary, sort of — see Patterns).

### E4 — `topics/gladmin-administrator-read-apis-gin-migration`

| NAV | ID | FAIL | DIAG | XREF | ACR | CODE |
|---|---|---|---|---|---|---|
| FAIL | PARTIAL | FAIL | FAIL | WEAK | FAIL | FAIL |

This is the clearest "API migration" page in the sample, so it's a fair bellwether for what the wiki teaches engineers about **APIs**. It teaches almost nothing concrete:

> "It targets read operations on Approval PG tables: gl_adm, gladm_user_profile, gladmin_emp_permission_history, gladmin_role_default_access, Iil_gladmin_perm_master, gladmin_screen_docs, gladm_modules, gl_districts, gl_city."

Good — that is real information. But for the **APIs themselves** the page says only:

> "Migrated all Administrator module Read APIs to Gin Context"
> "Request Handling | Gin Context (c.JSON, c.Bind, c.Param)"
> "Response Format | Unified GLADMIN-style JSON wrapper"

There is **not one route**, **not one example payload**, **not one curl invocation**. A new engineer cannot answer "what are the Administrator Read APIs?" — only that there are some and that they have been migrated. The "Influencer functionalities" reference is a parenthetical that begs the question.

> "Monitoring: Kibana dashboard: https://imkibanaindia.intermesh.net/app/r/s/ePE6G"

A short-link that is meaningless without already having Kibana SSO + the index-pattern context. Should be at minimum: dashboard *name*, what the saved view filters on (`service:gladmin-admin AND env:prod`?), and a Grafana alternative.

**FAIL on FAIL**: Page never says how the migration would fail, what 5xx-rate threshold rolls back, or how to roll back. "Backward compatibility" is asserted but not proved.

**Typo of `Iil_gladmin_perm_master`** — table name has a leading capital `I` followed by lowercase `il`; this is either real or a copy-paste error. An engineer running `psql` against this would have to guess. (Compare with E5: same table is `Iil_gladmin_perm_master` again — consistent at least, but still ambiguous.)

### E5 — `topics/gladmin-administrator-write-apis-gin-migration`

| NAV | ID | FAIL | DIAG | XREF | ACR | CODE |
|---|---|---|---|---|---|---|
| FAIL | PARTIAL | FAIL | FAIL | PARTIAL | FAIL | FAIL |

Sister page to E4 with **largely the same prose, fewer details**. The Read page lists 9 tables; the Write page lists 6. No reasoning given for the asymmetry — which tables are read-only and why? This matters for capacity planning and replication topology.

> "Centralized middleware for panics and validation"

Mentioned, not shown. A new Go engineer cannot tell whether the "centralized middleware" is `gin.Recovery()` plus a custom validator, or something bespoke that needs special handling.

> "Performance: Faster routing, reduced overhead"

Vacuous — no p50/p95/p99, no QPS, no benchmark vs the legacy `http.ResponseReader`. Compare with E6 which has a full before/after KPI table.

### E6 — `topics/msite-mcat-gke-migration`

| NAV | ID | FAIL | DIAG | XREF | ACR | CODE |
|---|---|---|---|---|---|---|
| PARTIAL | PARTIAL | PARTIAL | WEAK | PASS | PARTIAL | PARTIAL |

**The best technical page in the sample.** A week-2 engineer can come away with a real picture:

- Quantified before/after: 5xx/week 24 → 2 (92% reduction), CPU 14 → 6, MTTS 2d → <1min.
- Migration roadmap with monthly hits per module (Mcat ~44M, PDP ~25M, Company ~22M, Ajax ~522M).
- Sample URLs the engineer can hit and inspect (`https://m.indiamart.com/impcat/pen-camera.html`).
- Concrete tested GLIDs, test devices, browsers.
- Two named incident classes ("US bot traffic fallback to India servers", "Feature release disruptions").
- HPA, Docker, ConfigMaps/Secrets, rolling deployments, image-once-everywhere — these are real K8s primitives, used correctly.

What's still missing for an engineer:

- **No GCP project ID, no GKE cluster name, no namespace.** Where does the Mcat workload live? `gke-marketplace-prod`? `gke-msite-prod`? Unanswered. A new engineer with `gcloud` access cannot even point at the right cluster without asking.
- **No image registry** (`asia-south1-docker.pkg.dev/...`?), no service-account, no IAM role for deployments.
- **No HPA min/max replicas, no resource requests/limits.** "14 CPU → 6 CPU" — across how many pods?
- **No mermaid or topology diagram.** A list of K8s primitives is not the same as a picture of the request path (CDN → ingress → service → upstream IMPCAT API).
- **"Canary releases feasibility on GKE — raised by [[rounak-polley-indiamart-com]], pending response"** — a week-2 engineer reading this thinks: "wait, are we doing canaries or not?"
- The "Sanity testing identified three bugs" table has no link to the issue tracker by URL — only ticket IDs (`638853`, `639665`, `639667`).

**ACR**: HPA, MCAT, MSite, PDP, BL — all used without expansion or glossary-link.

### E7 — `topics/lms-replytowhatsapp-consumer-migration`

| NAV | ID | FAIL | DIAG | XREF | ACR | CODE |
|---|---|---|---|---|---|---|
| WEAK | PARTIAL | WEAK | FAIL | PARTIAL | FAIL | FAIL |

The page mentions a **Kafka topic by name** — `LMS_FANOUT` — which is the kind of identifier an on-call engineer needs. Good. But:

- No mention of Kafka cluster / brokers / partitions / consumer-group ID.
- No message schema or sample payload. "Sends text, images, videos, documents, templates to WhatsApp" — what's the payload boundary between LMS_FANOUT and the WhatsApp send-API consumer?
- "Migrated from PHP (RMQ-based) to Golang (LMS_FANOUT Kafka-based)" — the **single most important architectural fact** is one parenthetical. Where is the old RMQ queue? Is it drained? Is there a fallback? **For a migration this is the thing that pages.**
- "AutoRetry" is named, never specified. Retry budget? Backoff? DLQ topic name?
- 5 defects listed by ticket ID with one-line descriptions, but no severity, no SLA breach impact, no current status as of the wiki's `last_compiled`.
- **Bug 641243** ("Video compression broken; 99MB video uncompressed") would be a P0 capacity event in any other system. Page treats it as a routine QA defect.

**ACR**: LMS, RMQ (RabbitMQ, presumably) used without expansion. "IM Insta Pro (POC)" — POC referenced as if context-free.

### E8 — `topics/aisensy-indiamart-payload-architecture-enhancement`

| NAV | ID | FAIL | DIAG | XREF | ACR | CODE |
|---|---|---|---|---|---|---|
| FAIL | FAIL | FAIL | FAIL | WEAK | FAIL | FAIL |

The most disappointing page in the sample. Title literally contains "Payload Architecture" — a new engineer expects payload + architecture. Page contains:

- **Zero payloads.** No request shape, no response shape, no field names.
- **Zero architecture.** No box diagram, no sequence diagram, no description of the workflow that was "tightly coupled" or what got "decoupled".
- "intermittent processing glitches that caused increased webhook response times, unpredictable latency spikes" — descriptive but not measurable. What was p95 before and after?
- "Major KPIs show insignificant deviation within acceptable ranges" — for a webhook integration, the KPIs should be: webhook delivery success rate, p50/p95/p99 webhook latency, retries-per-message, DLQ depth. None of these are present.
- The auditor in the page itself (`[[mohak-saxena]]`) **literally asks the question this page should answer**:

  > "What technical metrics (e.g., latency p95, error rates) can be measured pre- and post-implementation?"

  As of this audit, that question is in `## Open questions` and **unanswered on the page**. The page documents a launch announcement, not the integration.

- AiSensy is the third-party. No vendor URL, no API doc URL, no auth model, no signing key/secret rotation policy.
- WA9696 vs WA8181 — channel identifiers used without explanation. What does the prefix mean? (Phone-number suffixes for two WhatsApp business accounts, almost certainly, but a new hire wouldn't know.)

A new engineer assigned a webhook bug here would have to start over from raw emails.

### E9 — `topics/centralized-runtime-app-permission-tracking` + `topics/deeplink-parsing-logic-improvement`

| NAV | ID | FAIL | DIAG | XREF | ACR | CODE |
|---|---|---|---|---|---|---|
| WEAK | FAIL | FAIL | FAIL | WEAK | PARTIAL | FAIL |

Both shipped in **Android v13.7.0**, both lazily cross-link each other ("launched in the same v13.7.0 release"), both share the same source email cluster — and both have the same blind spot for engineers.

Permission tracking page promises a **backend system, real-time tracking, dynamic permission discovery, single API call multi-permission**. A new Android or backend engineer wants to know:

- What is the API endpoint? (`POST /v1/user-permissions/sync`? Unknown.)
- What is the request body? Permission names + grant booleans, presumably, but unverified.
- What backend service owns the table? Where is the table?
- "Remote Config controlled for safe rollout" — Firebase Remote Config? Some in-house feature flag?
- "Local state updated post successful sync to avoid duplicate calls" — what's the dedup key? `(user_id, permission_name)` plus a version?

Deeplink page is **slightly better** because the "Before vs After" prose is a real description of the parsing logic (regex → sanitation → normalization → validation → extraction). But:

- No code snippet of either the old or new parser.
- No test suite or fixtures.
- "Sensitivity to URL structure where minor format deviations could cause failures" — example URLs that previously failed would be the single most useful thing on the page. Absent.
- The open question — `Requested examples of errors that previously cropped up and would now be handled long-term` — is the question this page should pre-empt.

## Patterns (recurrent technical-onboarding failure modes)

### P1 — Kibana-shortlink-as-monitoring-section

Pages E4, E5, E7 (and several others outside the sample) end the "Monitoring" section with a single hashed Kibana URL:
- `https://imkibanaindia.intermesh.net/app/r/s/ePE6G`
- `https://imkibanaindia.intermesh.net/app/r/s/66dUZ`

A short-link to a saved Kibana view is **not a monitoring section**. A new engineer cannot tell what index, what filter, what alert, what threshold, or what runbook is associated. The wiki should require, at minimum: dashboard name, the index pattern, a one-line description of what "green" looks like, and the alert name (if any).

### P2 — "We migrated to Gin" without a route or payload

E4 and E5 are nearly-identical templates (Why it matters / What has been done / Technical details / Testing / Impact / Recent changes), and yet **neither page contains a single API route, request body, or response example**. Compare to GitHub-quality READMEs for similar Go-on-Gin services. The wiki has `c.JSON, c.Bind, c.Param` cited as if those words by themselves teach the reader how to call the API.

### P3 — Tickets as a substitute for issue triage

Across all 9 pages there are **at least 18 ticket IDs** (`639598`, `622175`, `641243`, `640821`, `638853`, etc.). At most 2 of them link to a URL in the wiki. The rest are bare integers in tables. A new engineer can't even click. Wiki should standardize a footnote-style auto-linker: `[#639598](https://project.intermesh.net/work_packages/639598)`.

### P4 — Glossary actively hostile to engineers

`wiki/glossary.md` is in the wiki and contains entries like:
- `AI = GenUI Agent`
- `API = critical seller-facing service`
- `AM = previously 8:00 AM`
- `BL = Monolith + Modular`
- `CATALOG = changed 07/01`
- `BOT = for Buy Click on Photo Menu`
- `CSAM = exploits, abuses, or endangers children`

These are **regex-extracted snippets, not definitions**. A week-2 engineer who consults the glossary for "API" will be actively misled. This is worse than the glossary being absent — see also the precedent persona-deep-audit-deployed-2026-04-28 P1 ("a 5-line glossary entry would close this") which explicitly told a previous compile run to ship a curated glossary; it has been replaced by a broken auto-extracted one. **The acronym hygiene of every individual page rests on top of this broken layer.**

(Specifically broken for engineers in the sample: `MCP`, `SOA`, `FCP`, `SS+`, `RMQ`, `HPA`, `LMS`, `MSite`, `PDP`, `BL`, `MCAT`, `POC`, `WA9696`, `IM Insta`. None of these have a true 1-line definition that an engineer can use.)

### P5 — Identity gap: services have no repo / no namespace / no owner

None of the sampled pages contain:
- a GitHub or GitLab repo path,
- a Kubernetes namespace,
- an environment variable name,
- a GCP project ID,
- an image-registry path,
- a `service.yaml` reference,
- an AWS account/region,
- a single PagerDuty/Opsgenie alert name.

E1 even names "kubectl-ai" and "Grafana MCP" but doesn't tell the reader where their config lives. **Engineering-productivity tooling that doesn't tell engineers where its own code lives is failing its mission statement.**

### P6 — Bug tables ≠ failure modes

Every page that lists bugs lists them in a "QA found N defects" table. None of them say:
- which alert would have caught this in prod,
- the customer impact (how many sellers? what % of traffic?),
- the rollback decision threshold,
- the post-incident learning,
- the on-call escalation path.

Bugs are framed as **launch-readiness QA findings** ("16/18 passed"), not as **system failure modes a new engineer must understand**. Compare E7 Bug 641243 (uncompressed 99MB video to WhatsApp) — that's a capacity / cost / rate-limit incident waiting to happen. Page treats it as line-item QA.

### P7 — Bidirectional Related/Related drift

Almost every page has TWO `## Related` sections — a curated one (3–8 wikilinks, person-tagged) and an auto-generated one (every named entity blob-listed). E7 has both `## Related pages` (with 2 entries) and a footer `## Related` block that includes only `system/im-insta` and `system/lmshelp`. E8 ends without a footer Related — until you look closer and realize the YAML `related:` is also rendered into the page. This noise tells an engineer the wiki is auto-generated and untrustworthy. Pick one and burn the other.

### P8 — Diagrams: 0 mermaid blocks, 1 external link

Across 9 pages there is **one** diagram reference: in `whatsapp9696-agentic-buyer-chatbot` to a `app.diagrams.net` shared link (`https://app.diagrams.net/#G1iK7xj1HJahHXlLkqyXE2snlr_ofzDBYG`). Zero embedded mermaid, zero schema tables for message payloads, zero sequence diagrams. For a wiki whose explicit purpose is to teach "things in the world", the absence of pictures is striking. An engineer needs the box diagram more than the launch announcement.

## Top 3 follow-up PRs to ship (engineer-onboarding focused)

These are scoped to the deterministic post-compile pipeline / page templates — they don't require re-running the LLM agent.

### PR-A — Add a required "Engineering surface" frontmatter block, validated

Add to YAML frontmatter for every `system` page (and topic pages tagged `migration|infra|api|consumer`):

```yaml
engineering_surface:
  repo: "github.com/indiamart/gladmin-soa"
  service_name: "gladmin-admin-api"
  k8s:
    cluster: "gke-marketplace-prod"
    namespace: "gladmin"
  alerts:
    - name: "gladmin-admin-5xx-rate"
      runbook: "https://wiki/runbooks/gladmin-5xx"
  oncall: "soa-platform"
  tickets_project: "https://project.intermesh.net/projects/gladmin-soa"
```

Make these fields *optional but linted*. The `wiki-lint` job emits a warning per field missing, then a per-domain "engineering surface coverage" badge on `wiki/changes.md`. The agent is not asked to invent these — only to copy them when present in source emails (which they often are; the migration emails in run 3e88f996 mention service IDs, ticket projects, and Kibana dashboards by name in raw text). **This single change closes ~70% of the gap exposed by P5.**

Why now: the precedent persona-deep-audit (`persona-deep-audit-deployed-2026-04-28`) recommended `owner:` / `DRI:` for PMs; this PR generalizes it to engineers. Same machinery, broader payoff.

### PR-B — Auto-link ticket IDs and replace Kibana hash-shortlinks

Two small parsers in the post-compile coordinator:

1. Anywhere body text contains a 6-digit ticket ID (regex `\b\d{6}\b` near words like "ticket", "Bug", "Ticket"), rewrite to `[#639598](https://project.intermesh.net/work_packages/639598)`. Idempotent — skip if already linked.
2. Anywhere a `imkibanaindia.intermesh.net/app/r/s/<hash>` URL appears, lint-fail unless preceded by a one-liner naming the dashboard, the index, and the "what green looks like" sentence. Provide a snippet template the agent can fill at compile time.

Closes P1 + P3 across all 42 pages in this run, with no new agent prompt complexity.

### PR-C — Replace the broken regex glossary with a curated stub set + per-domain hub TL;DR

Two parts:

1. Take the current `wiki/glossary.md` and **delete all entries whose "expansion" is not a fluent definition**. A 30-line shell script flags entries where the expansion column contains any of: `previously`, `for Buy Click`, `display: none`, `+16.76%`, etc. (i.e. is clearly a regex-extracted phrase fragment, not a definition). Keep the 20–30 entries that *are* fluent (`AHT = Average Handling Time`, `BLNI = BuyLead Not Interested`, `CSL = Clickstream`).
2. Hand-write 1-line entries for the engineering acronyms that recur in the run-3e88f996 sample but have no fluent glossary entry today: **MCP** (Model Context Protocol), **SOA** (Service-Oriented Architecture), **FCP**, **HPA** (Horizontal Pod Autoscaler), **MCAT** (Master Category), **PDP** (Product Detail Page), **MSite** (Mobile Site), **BL** (BuyLead), **POC** (Proof of Concept), **RMQ** (RabbitMQ), **WA9696/WA8181** (WhatsApp 96/81-prefix business accounts), **SS+** (Star Supplier+), **PNS** (Phone Number Sharing), **NI** (Not Interested feedback). Cap at 30 entries. **Inline these in `wiki/glossary.md` and freeze the file** — disable the auto-extractor for it.
3. Each domain hub page (`wiki/domains/*.md`) gets a 5-line "What you need to know to read this domain" section listing domain-specific acronyms with one-line meanings.

Closes P4 directly. Cost: ~1 engineering hour. Value: every page in the wiki immediately becomes 30% more legible to a new hire.

---

**Honest one-line verdict for run 3e88f996 from an engineer's perspective**: pages are PR-quality launch digests; they do not survive contact with `kubectl get pods` or "where do I clone?". The fix is mostly in the coordinator, not the agent.

AUDIT: /Users/amtagrwl/git/email-knowledge-base/docs/audits/run-3e88f996-engineer-persona-audit-2026-04-29.md
