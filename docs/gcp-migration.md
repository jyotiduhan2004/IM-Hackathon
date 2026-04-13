# Migrate Email Knowledge Base to GCP — phased plan

> Also tracked in the /Users/amtagrwl/.claude/plans/linear-brewing-blum.md personal
> plan file. This copy lives in the repo so the team can see and edit it.
> Implementation artifacts referenced below:
> `Dockerfile`, `nginx.conf`, `.dockerignore`, `.gcloudignore`,
> `scripts/gcp/bootstrap.sh`, `scripts/gcp/deploy-viewer.sh`,
> `make publish` (in `Makefile`).

## Context

Phase 0 shipped: the CLI compiles 447 raw emails into 463 interlinked wiki pages, and `make wiki` serves them locally via MkDocs Material. Nothing in the repo touches GCP yet — no Dockerfile, no `cloudbuild.yaml`, no IaC. The `docs/BACKLOG.md` and `docs/issues/07-phased-delivery.md` already sketch a Phase 4 "Cloud Run + IAP" rollout; this plan executes it and then continues all the way to "everything on GCP".

The user wants to send a launch mail **this week** so the whole IndiaMart org can browse the wiki. Decisions locked during planning:

- **Launch scope**: read-only viewer only — ingest + compile stay manual on the laptop.
- **Source of truth for content**: a private GCS bucket holding `raw/` + `wiki/` (decouples content from the code repo so future cloud-side ingest can write to it without a git push).
- **Access**: IAP gated to the whole Workspace org initially; tighten to the mailing list's Google Group once that group exists.
- **Timeline for launch**: days. Minimum viable GCP footprint — no Postgres, no Pub/Sub, no auto-deploy on day one.
- **GCP project**: existing `voice-eval-stack-im`.
- **Eventual end state**: everything runs on GCP (ingest, compile, viewer, state). Getting live is urgent; the rest is iterated in.

Phase A is detailed enough to execute directly; **Phases B–E** get progressively lighter because scope will shift once real users touch the viewer.

## Phase map

| Phase | Goal | Target | Detail |
|---|---|---|---|
| A | Viewer live on GCP behind IAP | this week | full runbook below |
| B | Harden publishing + tighten access | weeks 2–3 | commands + rough sequencing |
| C | Automated ingest on GCP | month 1–2 | architecture + components |
| D | Compile on GCP, Postgres in Cloud SQL | month 2–3 | component sketch |
| E | Search, polish, multi-list | month 3+ | capability bullets |

Everything past Phase A is subject to rethinking after real users land. Don't over-commit now.

---

## Phase A — Ship the viewer (this week)

### A.0 Prerequisites (one-time, outside code)

1. Confirm Owner/Editor on `voice-eval-stack-im` and that the org policy allows Cloud Run public services + IAP.
2. Confirm the Workspace primary domain for the IAP grant (planning assumes `indiamart.com` — verify with `gcloud organizations list`).
3. OAuth consent screen / IAP brand. The IAP OAuth Admin API was permanently shut down **March 19, 2026**, so new brands can no longer be created via gcloud. Two paths:
   - **Existing brand on the project** — that's our case on `voice-eval-stack-im`. Brand `Indiamart AI` (org-internal-only, support email `aa@indiamart.com`) is live and keeps working; verify with `gcloud iap oauth-brands list --project=voice-eval-stack-im` while that (also deprecated) command still functions.
   - **No brand on a fresh project** — IAP now falls back to a Google-managed OAuth client by default, which restricts browser access to users within the same Workspace organization. No manual setup needed; just enable `--iap`. The Cloud Console is the only remaining path to configure a custom brand.
4. Pick a region — planning assumes `asia-south1` (Mumbai) for IndiaMart proximity.

APIs are enabled by `scripts/gcp/bootstrap.sh`.

### A.1 GCS bucket as durable wiki store (with versioning)

Today `wiki/*.md` is gitignored; `wiki/` structure is tracked. Once cloud-side services start writing to GCS in Phases C/D, those writes bypass git entirely — so we need GCS **Object Versioning** enabled from day one to preserve the snapshot property. Turning it on at creation is cheaper than retrofitting.

`scripts/gcp/bootstrap.sh` handles:

- Creating `gs://indiamart-email-kb` with `--uniform-bucket-level-access`, `--public-access-prevention`, `--versioning`.
- Applying a lifecycle rule that deletes noncurrent versions after 180 days.

Run once:

```bash
bash scripts/gcp/bootstrap.sh
# or
make bootstrap
```

Initial seed from laptop:

```bash
gsutil -m rsync -r raw/  gs://indiamart-email-kb/raw/
gsutil -m rsync -r wiki/ gs://indiamart-email-kb/wiki/
```

With versioning on, every subsequent rsync that overwrites an object creates a noncurrent version — `gsutil ls -a gs://indiamart-email-kb/wiki/index.md` lists all generations, and `gsutil cp gs://.../index.md#<generation>` restores any prior version. That's the GCS equivalent of `git log`/`git show` for `wiki/`.

Bucket is internal-only — the viewer does NOT serve from it directly in Phase A; it exists so future cloud-side ingest (Phase C) has somewhere to write without losing history.

### A.2 Containerize the static site

The viewer is just the output of `mkdocs build` served by nginx. Files at worktree root:

- **`Dockerfile`** — two stages. Builder: `python:3.12-slim`, `pip install mkdocs-material mkdocs-roamlinks-plugin pyyaml`, `COPY mkdocs.yml mkdocs_hooks.py wiki/`, `mkdocs build`. Runtime: `nginx:alpine`, `COPY nginx.conf` + built `site/`, `EXPOSE 8080`.
- **`nginx.conf`** — listens on 8080, gzip on, long cache on hashed assets, no-cache on HTML, `try_files $uri $uri/ $uri.html =404`.
- **`.dockerignore`** — excludes everything not needed to build the viewer image.
- **`.gcloudignore`** — same exclusion list as `.dockerignore`. **Required** because otherwise `gcloud run deploy --source .` falls back to `.gitignore`, which excludes `wiki/*.md` — and the image would ship empty.

Local smoke test:

```bash
docker build -t email-kb-viewer .
docker run --rm -p 8080:8080 email-kb-viewer
# open http://localhost:8080 — confirm wiki home + wikilinks render
```

### A.3 Deploy to Cloud Run

`scripts/gcp/deploy-viewer.sh` runs `gcloud run deploy --source .` which invokes Cloud Build on the Dockerfile and pushes to an auto-created Artifact Registry repo, then enables IAP and grants the Workspace domain:

```bash
bash scripts/gcp/deploy-viewer.sh
```

Effective commands inside the script:

```bash
gcloud run deploy email-kb-viewer \
  --project=voice-eval-stack-im \
  --region=asia-south1 \
  --source=. \
  --no-allow-unauthenticated \
  --iap \
  --memory=256Mi --cpu=1 \
  --max-instances=5 --min-instances=0 \
  --port=8080

gcloud iap web add-iam-policy-binding \
  --project=voice-eval-stack-im \
  --resource-type=cloud-run --service=email-kb-viewer --region=asia-south1 \
  --member='domain:indiamart.com' \
  --role='roles/iap.httpsResourceAccessor'
```

IAP-on-Cloud-Run is GA. `--iap` can be passed directly to `gcloud run deploy` in a single call — no separate update step, no `beta` component required. `gcloud iap web add-iam-policy-binding --resource-type=cloud-run` is also GA.

Override via env vars (`GCP_PROJECT`, `GCP_REGION`, `GCP_SERVICE`, `GCP_IAP_DOMAIN`) if any of the defaults are wrong.

### A.4 `make publish` update loop

Post-launch the cycle is: run ingest/compile on laptop → `make publish`. `Makefile` target:

```make
publish:
	uv run mkdocs build
	gsutil -m rsync -r raw/  gs://indiamart-email-kb/raw/
	gsutil -m rsync -r wiki/ gs://indiamart-email-kb/wiki/
	bash scripts/gcp/deploy-viewer.sh
```

One target runs the whole update flow.

### A.5 Verification (gate on launch mail)

1. `gcloud run services describe email-kb-viewer --region=asia-south1 --format='value(status.url)'` → prints a run.app URL.
2. `curl -sI <URL>` → `302` to `accounts.google.com`.
3. Incognito → sign in with `@indiamart.com` → wiki home renders.
4. Click a topic page → wikilinks resolve → "Sources" section renders at least one `<details>` block with email headers + body (a bare "Sources" heading with only `(file missing)` items means `raw/` didn't make it into the image — see `.dockerignore`/`.gcloudignore`).
5. Sign in with non-org Google account → `403`.
6. `gsutil ls gs://indiamart-email-kb/raw/ | wc -l` ≥ 447.
7. `gsutil cat gs://indiamart-email-kb/wiki/index.md | head -20` returns the catalog.
8. Cold-start check: hit `/` after ~15 min idle → first response < 5s. If not, bump `--min-instances=1`.

If any fail, the launch mail does not go out.

### A.6 Risk notes

- **OAuth consent screen misconfigured** is the single most common IAP blocker. If the smoke test 302s to Google and then errors, check this first.
- **Cold starts**: with `--min-instances=0`, first hit of the day is ~2–4s. Acceptable for a read-only wiki.
- **Attachment links will 404**: pages referencing `raw/attachments/...` won't render images because `.dockerignore` excludes `raw/attachments/`. Deliberate — fix in Phase B if complaints land. The `raw/*.md` email files themselves are included so every page's "Sources" block renders correctly.
- **Freshness**: viewer is only as current as the last `make publish`. Launch-mail talking point.
- **Default runtime service account**: Cloud Run uses the Compute Engine default service account out of the box, which has Editor on the project. Fine for Phase A (read-only static files), but swap to a dedicated service account with no permissions when we add any cloud-side read/write in Phase C.
- **History source-of-truth during Phase A**: git remains authoritative for `wiki/` while compile still runs on the laptop. GCS versioning is insurance for when that flips in Phase C/D.

---

## Phase B — Harden publishing + tighten access (weeks 2–3)

Triggered by: launch mail sent, early users giving feedback. Goals are "make updates painless" and "narrow the blast radius."

**What lands:**

1. **Cloud Build trigger on push to `main`** — replaces the `deploy-viewer.sh` step inside `make publish`. Trigger config: source = GitHub repo, included path filter = `wiki/**` + `Dockerfile` + `nginx.conf`, build = `gcloud run deploy --source .`. A `cloudbuild.yaml` at repo root captures the steps. The bucket rsync step stays on the laptop (no cloud-side writes yet).
2. **Tighten IAP** to a Google Group. Once `internal-ai-mailing-list@indiamart.com` (or whatever the list's group name is) exists, add `group:<group>@indiamart.com` with `roles/iap.httpsResourceAccessor` and remove the broad `domain:` grant.
3. **Custom domain** (optional) — `wiki.internal.indiamart.com` pointing at Cloud Run via domain mapping + Google-managed cert. Needs DNS access; skip if that's friction.
4. **Basic alerting** — Cloud Monitoring uptime check on the run.app URL, notification channel = launch mail author's email. One alert policy.
5. **Attachments decision** — either include `raw/attachments/` in the MkDocs build (copy to `site/attachments/`) or explicitly note they're unreachable. Not both.

**Files touched**: new `cloudbuild.yaml`, `Makefile` trim (`publish` → just builds + rsyncs bucket; deploy is now CI's job), maybe `mkdocs.yml` for attachments.

**Not yet**: no Secret Manager (no cloud secrets to hold yet), no Cloud SQL, no Pub/Sub.

---

## Phase C — Automated ingest on GCP (month 1–2)

Triggered by: manual `scripts/ingest_backlog.py` runs feeling annoying / emails showing up late in the viewer. This is the Phase 1 design from `docs/issues/08-phase1-live-ingestion.md`.

**Architecture sketch:**

```
Gmail watch  →  Pub/Sub topic  →  Cloud Run (email-kb-ingest)
                                        ↓
                         GCS: gs://indiamart-email-kb/raw/
                                        ↓
                                (catalog write — see Phase D)
```

**Components:**

- **Gmail watch registration** — a script (run once + re-upped weekly by Cloud Scheduler) calls `users.watch` against the mailing-list inbox, pointing at a Pub/Sub topic. Uses a Workspace service account with domain-wide delegation so the team doesn't depend on an individual's OAuth token.
- **Pub/Sub topic + push subscription** — `topic: gmail-inbound`, `subscription: gmail-inbound-to-ingest` pushing to the Cloud Run URL with OIDC auth.
- **Cloud Run service `email-kb-ingest`** — FastAPI endpoint handling Pub/Sub push. Reuses `src/ingest/gmail.py` + `src/ingest/parser.py` + `src/ingest/attachments.py` under a new webhook entrypoint in `src/api/`. Writes `.md` + attachments to `gs://indiamart-email-kb/raw/` using `gcsfs` or the storage client.
- **Secret Manager** — hosts the service account key (if one is needed) and Gmail OAuth refresh token. `email-kb-ingest` reads via `google-cloud-secret-manager`.

**Open decision at the time of Phase C:** does compile state live in GCS (a small JSON per email's compile status) or does it need Postgres already? If Postgres becomes load-bearing here, collapse Phase C and D.

**Laptop role after Phase C**: still runs compile; ingest is now automated.

---

## Phase D — Compile on GCP + Postgres in Cloud SQL (month 2–3)

Triggered by: compile-on-laptop being the last manual step; wanting the team to see new wiki pages without the owner running commands.

**Components:**

- **Cloud SQL for Postgres** (private IP, same VPC as Cloud Run, small tier — `db-g1-small` to start). Replaces the local `localhost:5432`. Schema from `src/db/schema.sql` applied via one-shot migration.
- **Cloud Run Job `email-kb-compile`** — runs `scripts/compile_all.py` on a schedule (Cloud Scheduler kicks it hourly, or triggered by ingest backlog depth crossing a threshold). Job reads Postgres for the queue, writes `.md` to GCS, updates Postgres state.
- **LiteLLM** — simplest: keep using the Intermesh endpoint; put the API key in Secret Manager. More ambitious: stand up a dedicated LiteLLM proxy in the same VPC. Start with the simple path.
- **Langfuse** — re-enable once issue #17 (OTLP flush stall) is resolved; point at the Intermesh endpoint or a self-hosted instance in GCP.
- **Viewer wiring** — viewer bucket path stays the same. A Cloud Build trigger on GCS object create (or a simple `gcloud run deploy` from the compile job's last step) refreshes the served site.

**Component diagram** (final end state after D):

```
Gmail → Pub/Sub → Cloud Run (ingest) → GCS raw/ + Cloud SQL
                                               ↓
                                Cloud Run Job (compile, scheduled)
                                               ↓
                                         GCS wiki/
                                               ↓
                                    Cloud Run (viewer, IAP)
```

Laptop is no longer in the hot path for anything — just development.

---

## Phase E — Search, polish, scale (month 3+)

Capability bullets only; sequencing decided when we get there.

- **Semantic search** — add pgvector to the Cloud SQL instance; index wiki pages on compile. New lightweight search API on Cloud Run, wired into the MkDocs template.
- **Multi-list support** — separate GCS prefixes and viewer services per mailing list; parameterize ingest by subscription.
- **Staging environment** — a second Cloud Run service (`email-kb-viewer-staging`) fed by a staging bucket for previewing wiki changes before publishing.
- **MCP server exposure** — wrap the compile tools as an MCP server so downstream agents can query the wiki programmatically (tracked in `docs/BACKLOG.md`).
- **Backup + DR** — GCS bucket versioning already on; add Cloud SQL point-in-time recovery when Cloud SQL lands in Phase D.
- **Cost monitoring** — billing budget + alert when monthly spend > threshold.
- **Access model upgrade** — finer-grained IAP grants (per-topic if that ever matters), audit logging of who views what.

---

## Execution order summary

Phase A is the only phase with a fixed deadline (this week). Everything after is demand-pulled:

- Do B when updates feel painful.
- Do C when manual ingest feels painful.
- Do D when manual compile feels painful.
- Do E when the basic thing is obviously not enough.

Don't pre-build any phase that's not pulling weight yet. The backlog stays the backlog until real friction shows up.
