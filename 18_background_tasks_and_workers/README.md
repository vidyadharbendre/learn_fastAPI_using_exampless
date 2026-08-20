# Day 18 — Background Tasks and Workers

> **Goal:** get slow work out of the request path — `BackgroundTasks` where it
> genuinely suffices, a real queue where it does not, and the retry, idempotency
> and visibility rules that keep "eventually" from meaning "sometimes never".
> **Time:** ~2.5 hours · **Port:** 8018 · **Builds on:** Day 17

> **Code status:** the README is the spec. Build `shelfspace/` yourself from
> sections 5 onwards before reaching for a reference implementation.

---

## 1. Why this matters

> **The user does not care that your welcome email was sent. They care that
> registration took four seconds and then failed because your SMTP server was
> slow.**

Work that is slow, flaky, or not needed for the response should not be in the
response path. But moving it out is where a specific class of silent failure
begins: the task runs on a machine nobody watches, fails with no listener, and
the only evidence is a support ticket three weeks later asking why a report never
arrived.

Today is about moving work off the request path *and* keeping it observable.

## 2. What you will build

```
18_background_tasks_and_workers/
├── run.py
├── worker.py                a standalone worker process
└── shelfspace/
    ├── tasks/
    │   ├── queue.py         enqueue/claim/complete over the database
    │   ├── handlers.py      send_email, generate_report, reindex
    │   └── retry.py         backoff, max attempts, dead letters
    ├── db/models.py         Job(status, attempts, run_at, result)
    └── api/v1/jobs.py       202 Accepted + a status endpoint to poll
```

## 3. Run it

```bash
source .venv/bin/activate
cd 18_background_tasks_and_workers
alembic upgrade head

python run.py            # terminal 1: the API
python worker.py         # terminal 2: the worker
```

Two processes. That separation is the day's main structural idea.

## 4. Try it — learn by doing

```bash
API=http://127.0.0.1:8018/api/v1
JSON='Content-Type: application/json'

# --- BackgroundTasks: the response returns immediately ---
curl -s -o /dev/null -w 'register: %{time_total}s\n' -X POST $API/auth/register \
  -H "$JSON" -d '{"email":"dana@example.com","password":"correct-horse-battery"}'
# the welcome email takes 2s — check the log AFTER the response landed

# --- a queued job: 202 Accepted + WHERE TO POLL ---
JOB=$(curl -si -X POST $API/reports -H "$JSON" -d '{"kind":"sales","month":"2026-08"}')
echo "$JOB" | head -6                                  # 202, Location: /jobs/<id>
ID=$(echo "$JOB" | tail -1 | python -c "import json,sys; print(json.load(sys.stdin)['job_id'])")

curl -s $API/jobs/$ID | python -m json.tool            # queued
sleep 3
curl -s $API/jobs/$ID | python -m json.tool            # running → succeeded + result URL

# --- retries with backoff, then a dead letter ---
curl -sX POST $API/jobs -H "$JSON" -d '{"kind":"always_fails"}' | python -m json.tool
watch -n1 "curl -s $API/jobs?kind=always_fails | python -m json.tool | head -30"
# attempts climb 1→2→3, run_at moves further out each time, then status=dead_letter

# --- idempotency: the same job twice does the work ONCE ---
for i in 1 2; do
  curl -s -X POST $API/reports -H "$JSON" \
    -d '{"kind":"sales","month":"2026-08","idempotency_key":"sales-2026-08"}' \
  | python -c "import json,sys; print(json.load(sys.stdin)['job_id'])"
done                                                   # the same job id twice

# --- BackgroundTasks does NOT survive a restart. Prove it. ---
curl -s -X POST $API/demo/slow-background -o /dev/null
# immediately Ctrl-C the API process. The task never completes, and nothing records that.

# --- a queued job DOES survive ---
curl -sX POST $API/jobs -H "$JSON" -d '{"kind":"slow","seconds":20}' | python -m json.tool
# Ctrl-C the worker, restart it: the job is picked up again

# --- two workers do not process the same job twice ---
python worker.py & python worker.py &
for i in $(seq 1 20); do curl -s -o /dev/null -X POST $API/jobs -H "$JSON" -d '{"kind":"noop"}'; done
sqlite3 shelfspace.db "SELECT status, COUNT(*) FROM jobs GROUP BY status;"
```

The "Ctrl-C the API" experiment is the one that decides which tool you use for
which job.

## 5. `BackgroundTasks` — what it is, exactly

```python
@router.post("/auth/register", status_code=201)
async def register(payload: RegisterIn, tasks: BackgroundTasks, ...):
    user = service.register(payload)
    tasks.add_task(send_welcome_email, user.email)     # runs AFTER the response
    return user
```

It runs **in the same process, after the response has been sent**. That gives it
one real advantage (zero infrastructure) and a list of hard limits:

| | |
|---|---|
| ✅ | no broker, no worker, no deployment change |
| ✅ | fine for fast, non-critical, fire-and-forget work |
| ❌ | **lost on restart, crash, or deploy** — no persistence at all |
| ❌ | no retries, no visibility, no way to check the result |
| ❌ | runs on the web server's CPU, competing with request handling |
| ❌ | a failure is invisible unless you log it yourself |

Use it for: a welcome email, a cache invalidation, an audit log write, a webhook
ping — where losing one occasionally is genuinely acceptable.

Do not use it for: anything a user is told happened, anything involving money,
anything that must be retried, anything slower than a second or two.

Two mechanics worth knowing:

- **`def` vs `async def` applies here too** (Day 14). A blocking `def` task runs
  in the thread pool; a blocking call inside an `async def` task freezes the loop
  *after* the response — which is harder to notice and just as damaging.
- **The dependency's `yield` teardown has already run.** A background task that
  uses the request's database session gets a closed session. Open its own.

```python
def send_report(user_id: int):                # ✅ its own session
    with SessionLocal() as session:
        ...
```

## 6. When you need a real queue

You need a queue as soon as any of these is true:

- The work must **not be lost** (payments, invoices, anything user-visible).
- It must be **retried** on failure.
- Someone needs to **see its status** — including you, during an incident.
- It is **slow** (minutes) or **CPU-heavy** (the web process should not do it).
- It must be **scheduled** (nightly reports) or **rate-limited** (a partner API).
- You want to **scale workers independently** of web servers.

| Option | Broker | Good for |
|---|---|---|
| `BackgroundTasks` | none | trivial fire-and-forget |
| **Database-backed queue** | your existing DB | small/medium apps — no new infrastructure |
| **ARQ** / **Dramatiq** | Redis | async-native, light, modern |
| **Celery** | Redis/RabbitMQ | the ecosystem standard; heavy, many features |
| **RQ** | Redis | simple, sync workers |
| **SQS/Pub-Sub + workers** | cloud | managed durability and scale |

**Start with the database-backed queue** unless you already run Redis. You get
durability, transactional enqueue (section 7), and one fewer system to operate —
and it is what you will build today, because building it teaches you what the
libraries are doing.

## 7. Enqueue inside the transaction

The subtle bug that queues create:

```python
# ❌ the email is sent for an order that was never committed
session.add(order)
queue.enqueue("send_confirmation", order_id=order.id)   # Redis: happens now
session.commit()                                        # this can still fail
```

With an external broker, the message is already gone when the transaction rolls
back. With a **database-backed** queue, the job row is written in the *same*
transaction — commit both or neither:

```python
session.add(order)
session.add(Job(kind="send_confirmation", payload={"order_id": order.id}))
session.commit()                                        # atomic
```

That is the "transactional outbox" pattern, and it is the strongest argument for
a database queue in a small system. If you use Redis/Celery, get the same effect
by enqueuing **after** commit (`session.commit()` then `enqueue`) and accepting
that a crash between them loses the job — or write an outbox row and have a
relay publish it.

## 8. The job model and claiming work safely

```python
class Job(Base):
    id:         Mapped[int] = mapped_column(primary_key=True)
    kind:       Mapped[str] = mapped_column(index=True)
    payload:    Mapped[dict] = mapped_column(JSON)
    status:     Mapped[str] = mapped_column(index=True)   # queued|running|succeeded|failed|dead_letter
    attempts:   Mapped[int] = mapped_column(default=0)
    max_attempts: Mapped[int] = mapped_column(default=5)
    run_at:     Mapped[datetime] = mapped_column(index=True)
    locked_by:  Mapped[str | None]
    locked_at:  Mapped[datetime | None]
    last_error: Mapped[str | None]
    idempotency_key: Mapped[str | None] = mapped_column(unique=True)
```

Two workers must never run the same job. The claim must be atomic:

```sql
-- PostgreSQL: the standard idiom
SELECT * FROM jobs
 WHERE status = 'queued' AND run_at <= now()
 ORDER BY run_at
 FOR UPDATE SKIP LOCKED
 LIMIT 1;
```

`FOR UPDATE SKIP LOCKED` lets each worker take a different row instead of
queueing behind the same one. Without it, a `SELECT` then `UPDATE` is a race and
you will process jobs twice under load.

Also handle the worker that dies mid-job: a `locked_at` older than a timeout gets
reclaimed. Which means **jobs can run twice**, which is why section 10 exists.

## 9. Retries, backoff, and dead letters

```python
DELAYS = [1, 5, 30, 120, 600]        # seconds — exponential-ish

def on_failure(job: Job, exc: Exception) -> None:
    job.attempts += 1
    job.last_error = f"{type(exc).__name__}: {exc}"[:500]
    if job.attempts >= job.max_attempts or isinstance(exc, PermanentError):
        job.status = "dead_letter"                 # stop; a human must look
        logger.error("job dead-lettered", extra={"job_id": job.id, "kind": job.kind})
    else:
        delay = DELAYS[min(job.attempts - 1, len(DELAYS) - 1)]
        job.run_at = utcnow() + timedelta(seconds=delay * jitter())
        job.status = "queued"
```

| Rule | Reason |
|---|---|
| **Exponential backoff** | retrying a struggling service every second is a DoS you are running |
| **Jitter** | without it, a thousand failed jobs retry in lockstep forever |
| **A maximum attempt count** | infinite retries hide a permanent failure |
| **Separate permanent from transient** | a 400 from an API will never succeed; do not retry it |
| **A dead-letter state, not deletion** | you need to inspect and replay it |
| **Alert on dead letters** | this is the silent failure the day opened with |

Distinguishing the two failure kinds is the part most implementations skip.
`ConnectionError` → retry. `ValidationError`, `404`, "card declined" → dead-letter
immediately; ten retries will not change the answer.

## 10. Idempotency: because "at least once" is what you get

Every queue that survives crashes delivers **at least once**. A job can run twice:
the worker died after the work but before marking it done; the lock expired while
it was still running; someone replayed a dead letter.

So the handler must be safe to run twice:

```python
def send_invoice(job_payload: dict) -> None:
    with SessionLocal() as session:
        order = session.get(Order, job_payload["order_id"])
        if order.invoice_sent_at is not None:      # ← the guard
            return
        provider.send(order)
        order.invoice_sent_at = utcnow()
        session.commit()
```

Patterns that make handlers idempotent:

- **A marker on the record** (`invoice_sent_at`) checked before acting.
- **A unique constraint** on whatever the work produces.
- **An idempotency key** passed to the downstream provider (Day 12) so *they*
  deduplicate.
- **Natural idempotency**: `SET status = 'shipped'` is safe; `stock = stock - 1`
  is not.

And the ordering rule: **do the external side effect last, or make it
idempotent**. If you email first and commit second, a crash in between means the
email is sent again on retry.

## 11. Telling the client: `202` and a status endpoint

```python
@router.post("/reports", status_code=202)
async def request_report(payload: ReportIn, response: Response, ...) -> JobAccepted:
    job = queue.enqueue("generate_report", payload.model_dump(),
                        idempotency_key=payload.idempotency_key)
    response.headers["Location"] = str(request.url_for("get_job", job_id=job.id))
    return JobAccepted(job_id=job.id, status="queued",
                       poll_url=f"/api/v1/jobs/{job.id}")
```

`202 Accepted` means "received, not done" — and it is only useful if you say
**where to look**. Then:

```json
GET /api/v1/jobs/42
{"id": 42, "status": "succeeded", "attempts": 1,
 "result": {"download_url": "/api/v1/reports/42.csv"},
 "created_at": "…", "finished_at": "…"}
```

Polling is fine and simple. For long jobs, offer a webhook or SSE (Day 20) so the
client is not polling every second for four minutes. Either way, **never leave a
client with no way to find out** — that is how "it silently failed" becomes your
support burden.

## 12. Running and watching workers

```bash
python worker.py --queues default,emails --concurrency 4
```

| Concern | Do |
|---|---|
| **Graceful shutdown** | handle `SIGTERM`: finish the current job, do not claim more |
| **Deploys** | in-flight jobs must survive; the claim timeout covers a hard kill |
| **Scaling** | more worker processes; the queue table is the coordination point |
| **Separate queues** | a slow report queue must not block password-reset emails |
| **Scheduling** | a `run_at` in the future gives you delayed jobs for free; a cron process enqueues periodic ones (Day 21) |
| **Health** | a worker heartbeat row, so you can alert when no worker is alive |

The metrics that matter, and the alerts on them:

- **Queue depth** — growing means workers cannot keep up.
- **Oldest queued job age** — the number your users actually feel.
- **Failure and dead-letter rate** — alert on any dead letter.
- **Job duration by kind** — where to optimise.

> **A worker with no monitoring is a place where work goes to disappear
> quietly.** Alerting on dead letters and oldest-job-age is the minimum.

## 13. Best practices introduced today

| Practice | Reason |
|---|---|
| `BackgroundTasks` only for losable, fast work | it does not survive a restart |
| A real queue for anything user-visible or retryable | durability is the whole point |
| Database-backed queue before adding a broker | fewer systems, transactional enqueue |
| Enqueue inside the transaction (or strictly after commit) | no jobs for rolled-back data |
| Background tasks open their own session | the request's session is already closed |
| `FOR UPDATE SKIP LOCKED` to claim jobs | a SELECT-then-UPDATE is a race |
| Reclaim jobs from dead workers by lock age | otherwise work stalls silently |
| Exponential backoff **with jitter** | lockstep retries are a self-inflicted DoS |
| Separate permanent from transient failures | retrying a 400 forever helps nobody |
| Dead-letter, never delete | you need to inspect and replay |
| **Every handler idempotent** | delivery is at-least-once, always |
| External side effect last, or keyed | a crash mid-handler must not double-send |
| `202` + `Location` + a status endpoint | never leave a client unable to find out |
| Separate queues by priority | a slow report must not delay a password reset |
| Handle `SIGTERM` gracefully | deploys happen while jobs are running |
| Alert on dead letters and oldest-job age | this is where silent failure lives |
| Small, serialisable payloads (ids, not objects) | the object may be stale by the time it runs |

## 14. Common mistakes

| Symptom | Cause | Fix |
|---|---|---|
| Emails vanish after a deploy | `BackgroundTasks` | use a queue |
| Task failed and nobody knew | no logging or status | log, record, alert |
| Confirmation for an order that does not exist | enqueued before commit | enqueue in/after the transaction |
| `DetachedInstanceError` in a background task | reused the request session | open a new one |
| Job processed twice by two workers | non-atomic claim | `FOR UPDATE SKIP LOCKED` |
| A job stuck in `running` forever | worker died holding the lock | reclaim by `locked_at` age |
| Customer charged twice | non-idempotent handler | guard with a marker or key |
| A failing job retried 4 million times | no max attempts | cap and dead-letter |
| Retry storm took down an upstream | no backoff or jitter | add both |
| Permanent errors retried forever | no error classification | fail fast on permanent |
| Password-reset emails delayed by an hour | one queue behind a big report | separate queues |
| Web server slow while jobs run | tasks on the web process | separate worker processes |
| Client never learns the outcome | fire-and-forget with no status | `202` + poll URL |
| Jobs lost on every deploy | no `SIGTERM` handling | drain gracefully |
| Payload references a deleted row | passed a whole object / stale data | pass ids, re-read |
| Queue silently 40,000 deep | no depth metric | alert on depth and age |
| Worker "running" but idle | no heartbeat | record one |

## 15. Exercises

1. Send the welcome email with `BackgroundTasks`, then kill the API immediately
   after registering and confirm it never arrives — and that nothing recorded the
   loss.
2. Build the `Job` table, `enqueue`, and a worker loop. Move the email to it and
   repeat the kill test.
3. Implement claiming with `FOR UPDATE SKIP LOCKED` on PostgreSQL, run four
   workers against 100 jobs, and prove every job ran exactly once.
4. Add backoff with jitter, a max attempt count, and a dead-letter state. Then
   add an endpoint to replay a dead letter.
5. Classify failures: make `always_fails_permanently` dead-letter on the first
   attempt while `flaky` succeeds on the third.
6. Make `send_invoice` idempotent, then deliberately run it twice and confirm one
   email.
7. Implement `202` + `Location` + `GET /jobs/{id}` and drive the full poll cycle
   from a client script.
8. Expose queue depth and oldest-job-age as metrics, then generate a backlog and
   watch them move. (Day 21 wires them into a dashboard.)

## 16. What's next

**[Day 19 — File Uploads and Streaming →](../19_file_uploads_and_streaming/)**
Reports need to be downloaded and book covers need to be uploaded. Tomorrow:
multipart uploads that cannot exhaust memory, validation that does not trust the
filename or the content type, and streaming responses for files too big to hold.
