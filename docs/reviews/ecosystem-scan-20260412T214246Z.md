# Ecosystem scan — 2026-04-12

Read-only scan around our stack (Karpathy LLM Wiki + Deep Agents + LiteLLM +
MkDocs Material + Langfuse). What shipped since the April 2026 gist, what
would invalidate us, and what to try next 48h.

## 1. LLM Wiki ecosystem

The gist spawned production riffs. Best one: [rohitg00's "LLM Wiki v2"][wiki-v2]
extends the pattern with (a) per-fact confidence scores, (b) supersession
records (new claim marks old stale, both retained + timestamped), (c) typed
entity graph ("uses"/"depends on"/"contradicts") instead of raw backlinks,
(d) BM25 + vector + graph fusion past ~100 pages. [llmwiki on PyPI][llmwiki]
ships an `eval` command emitting a 7-check quality score /100 plus `/kb-lint`
that flags "thin" articles (<3 substantive sentences) and proposes stubs —
directly applicable to our audit loop. Compiler repos worth diffing:
[ussumant/llm-wiki-compiler][wiki-compiler-ussumant] (Claude Code plugin) and
[atomicmemory/llm-wiki-compiler][wiki-compiler-atomic] (two-phase:
extract-all-concepts, then write pages). The two-phase ordering is cleaner
than our per-thread flow.

## 2. Deep Agents updates

[Deep Agents v0.5][da-v0.5] (Mar–Apr 2026) adds `AsyncSubAgent` specs and
five management tools (`start_async_task`, `check_async_task`,
`update_async_task`, `cancel_async_task`, `list_async_tasks`) — supervisor
fans out non-blocking work and collects by task ID. Exactly the primitive for
thread-batched compilation; replaces synchronous await loops. Multi-modal
filesystem backend now handles PDFs/audio/video (not relevant yet). v0.5
notes are silent on frontmatter safety / per-file permissions. `FilesystemBackend`
sits alongside `StateBackend`, `LocalShellBackend`, `StoreBackend`,
`CompositeBackend` in the [backend docs][da-backends]; `CompositeBackend`
(raw/ read-only, wiki/ write-allowed) is the right primitive for us.

## 3. Email-specific RAG / compilation

[mindsdb/email_rag][email-rag] is stagnant (same T.I.M.E framing, no 2026
activity). No net-new email-specific compiler surfaced. Adjacent: [sage-wiki][sage]
(Go, compiles mixed PDF/md/office/code folders into an interlinked wiki —
closest in spirit, albeit generic). [RAGFlow][ragflow] has momentum as a
document-understanding layer we could slot in front of ingestion if thread
structure outgrows mbox. Verdict: we're on the frontier of this niche.

## 4. Trivial-message filtering

No off-the-shelf "ack / +1 / thanks" classifier surfaced — the space is all
spam filtering (Naive Bayes / TF-IDF), wrong problem. Best applicable pattern
is [blakecrosley's Signal Scoring Pipeline][signal-scoring]: deterministic
composite score (length, novelty, attachment, etc.), then an "ambiguous zone"
(0.30–0.55) routed to an LLM `--llm-triage` pass that adjusts by ±0.20. In
their case, triaging only the ~5% ambiguous (420/7700) dropped cost from
$150–300 to $8–17. Directly portable: cheap heuristics kill obvious acks,
only borderline messages get an LLM call.

## 5. Langfuse self-hosted

Langfuse v3 self-host needs ClickHouse + Redis/Valkey + S3/MinIO + Web
containers; [production guidance][langfuse-scaling] is 2 CPU / 4 GB per
container, 16 GiB for ClickHouse, auto-scale at 50% CPU. Real-world
[estimates][langfuse-cost] put medium self-host at $3–4k/mo vs $199–300/mo for
Cloud Pro at 500k–2M events — self-host is a residency/legal move, not a cost
move. Alternatives: [Arize Phoenix][phoenix] (OSS, OpenTelemetry-native, no
trace limits — matches the voice-eval-stack sibling's OTel story) and
[Helicone][helicone] (one-line proxy, fastest setup, single point of failure).
Given our tiny scale and existing OTel footprint, **Phoenix is likely a better
fit than Langfuse** if wiring hasn't started.

## 6. Anthropic tool-writing guide

[Writing effective tools for AI agents][anthropic-tools] is still the
reference. Notable rubric item: Claude tends to **undertrigger** skills, so
make descriptions slightly pushy. The [Complete Guide to Building
Skills][anthropic-skills-pdf] adds the Claude-A-builds-for-Claude-B workflow
(one instance authors, another executes for eval). No v2 has landed.
Community rubric: [obra/superpowers][superpowers]
(`skills/writing-skills/anthropic-best-practices.md`).

## Top 3 adoptions for next 48h

1. **Stub-fill + lint command** modeled on [llmwiki][llmwiki]'s `/kb-lint` —
   flags thin articles (<3 sentences) and orphans, auto-drafts stubs. Smallest
   win; pure additive; plugs into our existing audit pipeline.
2. **Signal-Score-then-LLM-Triage for trivial filtering**
   ([pattern][signal-scoring]) — deterministic scoring first, LLM only on the
   ambiguous zone. Replaces any current "send every thread to the model"
   instinct; 10–20x cost reduction at our expected volume.
3. **Swap to async subagents via Deep Agents v0.5** ([release][da-v0.5]) —
   `AsyncSubAgent` + `start_async_task`/`check_async_task` unlocks parallel
   per-thread compilation without writing our own job queue. Also: revisit
   Phoenix instead of Langfuse if wiring hasn't started.

## Would any of this invalidate us?

No. Compiler pattern is sound; MkDocs Material + roamlinks is still idiomatic
(though [mkdocs-ezlinks][ezlinks] is more actively maintained if roamlinks
goes stale); Python 3.12 + uv + ruff + mypy strict is untouched. Only hedge
is observability — if Langfuse isn't wired yet, Phoenix is the better pick for
our OTel-native sibling stack.

[wiki-v2]: https://gist.github.com/rohitg00/2067ab416f7bbe447c1977edaaa681e2
[llmwiki]: https://pypi.org/project/llmwiki/
[wiki-compiler-ussumant]: https://github.com/ussumant/llm-wiki-compiler
[wiki-compiler-atomic]: https://github.com/atomicmemory/llm-wiki-compiler
[da-v0.5]: https://blog.langchain.com/deep-agents-v0-5/
[da-backends]: https://docs.langchain.com/oss/python/deepagents/overview
[email-rag]: https://github.com/mindsdb/email_rag
[sage]: https://toolhunter.cc/tools/sage-wiki
[ragflow]: https://www.firecrawl.dev/blog/best-open-source-rag-frameworks
[signal-scoring]: https://blakecrosley.com/blog/signal-scoring-pipeline
[langfuse-scaling]: https://langfuse.com/self-hosting/configuration/scaling
[langfuse-cost]: https://checkthat.ai/brands/langfuse/pricing
[phoenix]: https://www.confident-ai.com/knowledge-base/top-langfuse-alternatives-and-competitors-compared
[helicone]: https://www.helicone.ai/blog/best-langsmith-alternatives
[anthropic-tools]: https://www.anthropic.com/engineering/writing-tools-for-agents
[anthropic-skills-pdf]: https://resources.anthropic.com/hubfs/The-Complete-Guide-to-Building-Skill-for-Claude.pdf
[superpowers]: https://github.com/obra/superpowers/blob/main/skills/writing-skills/anthropic-best-practices.md
[ezlinks]: https://github.com/orbikm/mkdocs-ezlinks-plugin
