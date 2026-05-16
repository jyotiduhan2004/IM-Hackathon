---
title: Project Beta
page_type: topic
status: current
sources: []
related: []
---

# Project Beta

Project Beta is a smaller, follow-on effort focused on backfilling historical data into the warehouse after Project Alpha stabilises the ingestion pipelines. The scope of Beta is intentionally narrow: we pick the top-volume source systems and run them through a single-shot replay job using the same transformation code that powers the live pipelines. No new features are in scope — the primary deliverable is an audit of coverage and a short writeup explaining any gaps we could not close in this pass.
