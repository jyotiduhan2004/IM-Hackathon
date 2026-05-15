---
name: content-filter
description: "Load when processing raw email content. Strips irrelevant content: test results, signatures, scheduling, deployment logs, forwarded chains, greetings, bare ticket refs, CC lists."
metadata:
  type: compile
  version: 1.0
---

## Two-Stage Filtering

### Stage 1: Pre-Compilation (Deterministic)
Run `scripts/regex_prefilter.py` before the compiler agent sees the email. This strips:
- Email signatures (lines after "-- ", "Regards,", "Thanks,", "Best,")
- Forwarded chain markers ("---------- Forwarded message ----------")
- Quoted reply content (lines starting with ">")
- HTML artifacts and disclaimer blocks
- Personal greetings ("Hi team", "Hope you're doing well", "Dear all")
- CC/distribution list headers

### Stage 2: Compiler Judgment (This Skill)
When writing wiki content from emails, SKIP the following:

**Skip entirely:**
- Raw test case output (individual test results, pass/fail tables with 50+ rows)
- Meeting scheduling details ("Let's meet at 3pm", "Available on Thursday?")
- Deployment commands, server logs, stack traces, CLI output
- Bare Jira/ticket numbers without description (e.g., "Fixed in JIRA-1234")

**Keep summary only:**
- Test results → "X out of Y tests passed" or "All tests green"
- Deployment details → "Deployed to production on DATE" (skip the commands)
- Ticket references → only if accompanied by a description of what the ticket covers

**Always preserve:**
- Launch announcements and feature descriptions
- Architecture decisions and technical design
- Metrics, KPIs, performance numbers
- Rollout plans and timelines
- Business impact and user-facing changes
- Known issues and open questions
- Team ownership and accountability

## Gotchas
- When in doubt, preserve. Better to have slightly verbose wiki pages than to lose information.
- Some "test results" contain important metrics (latency P95, error rates). Preserve the metrics, skip the raw output.
- Email threads about a single topic may have 5-10 replies. Extract the final consensus, not every intermediate discussion.
