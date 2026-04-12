# Issue: Phase 1 — Live Ingestion via Gmail Watch + Pub/Sub

**Labels**: `feature`, `phase-1`

---

## Overview

Replace manual `ingest_backlog.py` with live, automatic ingestion. When a new email arrives
on the mailing list, it should be ingested and compiled within minutes.

## Architecture

```
Gmail Mailbox
    │
    │ users.watch() registers Pub/Sub topic (renewed every 6 days)
    │
    ▼
Google Cloud Pub/Sub
    │
    │ Push subscription → HTTPS POST
    │
    ▼
FastAPI Webhook (/webhook/gmail)
    │
    │ Receives { emailAddress, historyId }
    │ Calls history.list(startHistoryId=last_known)
    │ Gets list of new message IDs
    │
    ├── For each new message:
    │     ├── get_message() → parse → save to raw/
    │     └── Queue for compilation
    │
    ├── Quiet period check (30 min since last thread activity)
    │     └── If quiet: compile affected threads → update wiki/
    │
    └── git commit changes
```

## Gmail watch setup

```python
def start_watch(service, topic_name: str, user_id: str = "me") -> dict:
    return service.users().watch(
        userId=user_id,
        body={
            "topicName": topic_name,
            "labelIds": ["INBOX"],
            "labelFilterBehavior": "INCLUDE",
        },
    ).execute()
```

Must be renewed before 7-day expiry. Recommended: renew every 6 days.

## Pub/Sub setup

```bash
gcloud pubsub topics create gmail-email-kb

gcloud pubsub topics add-iam-policy-binding gmail-email-kb \
    --member="serviceAccount:gmail-api-push@system.gserviceaccount.com" \
    --role="roles/pubsub.publisher"

gcloud pubsub subscriptions create gmail-email-kb-push \
    --topic=gmail-email-kb \
    --push-endpoint=https://your-domain.com/webhook/gmail \
    --ack-deadline=60
```

## FastAPI webhook (stub)

```python
@app.post("/webhook/gmail")
async def gmail_webhook(request: Request):
    envelope = await request.json()
    data = base64.b64decode(envelope["message"]["data"]).decode()
    notification = json.loads(data)

    history_id = notification["historyId"]
    new_messages = fetch_new_messages(history_id)

    for msg in new_messages:
        await ingest_single_message(msg)

    await compile_quiet_threads()
    return {"status": "ok"}
```

## Thread quiet period

Don't compile a thread immediately — wait for the conversation to settle:

1. New email arrives → note thread_id and timestamp
2. Set 30-minute quiet timer for that thread
3. If another email arrives in same thread → reset timer
4. When timer expires → compile all uncompiled emails in that thread
5. Prevents compiling mid-conversation where context is incomplete

## Watch renewal

Option 1 — background task in FastAPI:
```python
@app.on_event("startup")
async def schedule_watch_renewal():
    async def renew_loop():
        while True:
            await asyncio.sleep(6 * 24 * 60 * 60)
            start_watch(service, topic_name)
    asyncio.create_task(renew_loop())
```

Option 2 — external cron (more reliable):
```
0 0 */6 * * curl -X POST http://localhost:8000/admin/renew-watch
```

## historyId state

Track last processed historyId to avoid reprocessing:

```python
HISTORY_STATE_FILE = ".gmail_history_id"

def get_last_history_id() -> str | None:
    if Path(HISTORY_STATE_FILE).exists():
        return Path(HISTORY_STATE_FILE).read_text().strip()
    return None
```

## Local development

For local dev without public HTTPS:

**Option A — Pull subscription** (no webhook needed):
```python
subscriber = pubsub_v1.SubscriberClient()
subscription_path = "projects/.../subscriptions/gmail-email-kb-pull"
future = subscriber.subscribe(subscription_path, callback=process_notification)
```

**Option B — ngrok tunnel**:
```bash
ngrok http 8000
# Use the ngrok URL as the push endpoint
```

## Reference implementation

[sangnandar/Realtime-Gmail-Listener](https://github.com/sangnandar/Realtime-Gmail-Listener) —
Complete serverless reference: Gmail watch → Pub/Sub → Cloud Run webhook. Auto-renews.
Under $2/month for 1,000 daily emails.

## Acceptance criteria

- [ ] Gmail watch registered, Pub/Sub notifications received
- [ ] FastAPI webhook processes incoming notifications
- [ ] New emails auto-ingested within 5 minutes of arrival
- [ ] Thread quiet period prevents mid-conversation compilation
- [ ] Watch auto-renewed before 7-day expiry
- [ ] historyId tracked to avoid reprocessing
- [ ] Works locally via pull subscription or ngrok
