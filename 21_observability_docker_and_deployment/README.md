# Day 21 — Observability, Docker and Deployment

> **Goal:** ship it — structured logs you can query, metrics and traces that
> answer "why is it slow", a small safe Docker image, Gunicorn + Uvicorn workers,
> real probes, CI, and a checklist for everything the last twenty days built.
> **Time:** ~3 hours · **Port:** 8021 · **Builds on:** Day 20

> **Code status:** the README is the spec. Build the deployment artefacts
> yourself from sections 5 onwards.

---

## 1. Why this matters

> **Production is not "the code, but on a different machine". It is the first
> place your application is asked questions it cannot answer.**

Which build is running? Why did that request take nine seconds? Which customer
hit the error at 14:02? Is the queue growing? Is the database pool exhausted, or
is it the upstream?

An application that cannot answer those is not "mostly done" — it is a system you
will be debugging by guesswork, at night, while it is broken. Today makes it
answerable, then packages and ships it.

## 2. What you will build

```
21_observability_docker_and_deployment/
├── Dockerfile              multi-stage, non-root, small
├── docker-compose.yml      api + postgres + redis + worker
├── gunicorn.conf.py        production process manager
├── .dockerignore
├── .github/workflows/ci.yml
└── shelfspace/
    ├── core/
    │   ├── logging.py      JSON logs, request_id on every line
    │   ├── metrics.py      Prometheus counters/histograms
    │   └── tracing.py      OpenTelemetry spans
    └── api/v1/health.py    /health/live and /health/ready
```

## 3. Run it

```bash
cd 21_observability_docker_and_deployment

docker compose up --build
curl -s http://127.0.0.1:8021/health/ready | python -m json.tool
curl -s http://127.0.0.1:8021/metrics | head -20
```

## 4. Try it — learn by doing

```bash
API=http://127.0.0.1:8021

# --- logs are JSON, one object per line, with the request id ---
docker compose logs api | tail -3 | python -m json.tool

# --- and every line of one request shares the id (Day 13) ---
RID=$(curl -sI $API/api/v1/books | awk '/[Xx]-[Rr]equest-[Ii]d/{print $2}' | tr -d '\r')
docker compose logs api | grep "$RID"

# --- liveness vs readiness are DIFFERENT questions ---
curl -s $API/health/live  | python -m json.tool      # is the process alive?
curl -s $API/health/ready | python -m json.tool      # can it serve? (deps checked)
docker compose stop db
curl -s -o /dev/null -w 'live  %{http_code}\n' $API/health/live    # still 200
curl -s -o /dev/null -w 'ready %{http_code}\n' $API/health/ready   # 503
docker compose start db

# --- metrics ---
for i in $(seq 1 50); do curl -s -o /dev/null $API/api/v1/books; done
curl -s $API/metrics | grep -E 'http_requests_total|http_request_duration_seconds_bucket' | head

# --- the image is small and runs as a non-root user ---
docker images | grep shelfspace
docker compose exec api whoami          # NOT root
docker compose exec api id

# --- graceful shutdown: in-flight requests finish ---
curl -s "$API/api/v1/demo/slow?seconds=5" &
sleep 1 && docker compose stop -t 30 api
# the request completes; the log shows a clean shutdown, no 502

# --- the app REFUSES to start misconfigured ---
docker compose run --rm -e SHELFSPACE_ENVIRONMENT=production \
  -e SHELFSPACE_SECRET_KEY=short api python -c "from shelfspace.main import create_app; create_app()"

# --- and docs are off in production ---
docker compose run --rm -e SHELFSPACE_ENVIRONMENT=production -p 8099:8021 api &
curl -s -o /dev/null -w 'docs %{http_code}\n' http://127.0.0.1:8099/docs
```

## 5. Logs you can actually query

```python
# core/logging.py
class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": request_id_ctx.get(),      # Day 13's ContextVar
            "service": "shelfspace",
            "version": __version__,
            "env": settings.environment,
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        payload.update(getattr(record, "extra_fields", {}))
        return json.dumps(payload)
```

| Rule | Reason |
|---|---|
| **JSON, one object per line** | `grep` does not scale; your log platform indexes fields |
| **`request_id` on every line** | the only way to reconstruct one request |
| **`version` and `env` on every line** | "was this the new build?" answered instantly |
| **Log to stdout, never to files** | the container runtime collects stdout; files fill disks |
| **No rotation inside the container** | that is the platform's job |
| **Never log secrets, tokens, bodies or PII** | logs are retained for a year and widely readable |
| **`logger.exception` in handlers** | a stack trace without one is a mystery |
| **Sample high-volume debug logs** | logging is a real cost at scale |

Levels, used honestly: `DEBUG` local only · `INFO` request lines and lifecycle ·
`WARNING` recoverable oddities (a retry, a slow query) · `ERROR` needs a human ·
`CRITICAL` the service is down. If everything is `ERROR`, nothing is.

## 6. Metrics: the four you actually watch

```python
REQUESTS = Counter("http_requests_total", "…", ["method", "path", "status"])
LATENCY  = Histogram("http_request_duration_seconds", "…", ["method", "path"])
INFLIGHT = Gauge("http_requests_in_flight", "…")
```

The **RED** method covers a service: **R**ate, **E**rrors, **D**uration — plus
saturation (pool usage, queue depth, memory).

> **Label with the route *template* (`/books/{id}`), never the actual path.**
> `/books/1`, `/books/2`… as separate label values is a cardinality explosion that
> will take down your metrics backend before it takes down your app. Use
> `request.scope["route"].path`.

Beyond HTTP, export what the previous days created:

| Metric | Alert when |
|---|---|
| DB pool in-use / size (Day 09) | consistently near the limit |
| Queue depth and oldest job age (Day 18) | growing, or older than an SLA |
| Dead-letter count (Day 18) | **any** |
| WebSocket connections (Day 20) | far from the norm in either direction |
| Rate-limit rejections (Day 13) | spiking (attack, or a limit set too low) |
| Auth failures (Day 15) | spiking (credential stuffing) |
| Event-loop lag (Day 14) | above ~50 ms |

Alert on **symptoms users feel** — error rate, p99 latency, queue age — not on
CPU. High CPU with happy users is not an incident.

## 7. Tracing, when logs are not enough

```python
FastAPIInstrumentor.instrument_app(app)
SQLAlchemyInstrumentor().instrument(engine=engine)
HTTPXClientInstrumentor().instrument()
```

Logs tell you *what* happened; traces tell you *where the time went*. One trace
shows the request split into spans: 4 ms in your handler, 780 ms in one query,
2.1 s waiting on the pricing API. That answers "why is it slow" in seconds
instead of an afternoon.

Propagate the trace context (`traceparent`) between services, and put the
`trace_id` in your log lines so a log and a trace can be joined. Sample: 100% of
errors and slow requests, a small percentage of the rest.

## 8. Liveness and readiness are different questions

```python
@router.get("/health/live")
async def live() -> dict:
    return {"status": "ok", "version": __version__}          # NO dependencies

@router.get("/health/ready")
async def ready(session: SessionDep) -> Response:
    checks = {}
    try:
        session.execute(text("SELECT 1")); checks["database"] = "ok"
    except Exception:
        checks["database"] = "error"
    ...
    healthy = all(v == "ok" for v in checks.values())
    return JSONResponse({"status": "ok" if healthy else "degraded", "checks": checks},
                        status_code=200 if healthy else 503)
```

| Probe | Question | If it fails | Must not |
|---|---|---|---|
| **Liveness** | is the process alive? | the container is **restarted** | touch any dependency |
| **Readiness** | can it serve traffic? | it is **removed from the pool** | be slow or uncached |
| **Startup** | has it finished booting? | delays the other two | — |

> Day 01 warned about this and it is worth repeating: **a liveness probe that
> checks the database restarts every container when the database hiccups**,
> turning a brief degradation into a full outage. Liveness answers only "is this
> process running".

Cache readiness results for a few seconds — a probe every 2 seconds across 20
replicas is 10 extra queries a second forever.

## 9. The Dockerfile

```dockerfile
FROM python:3.12-slim AS builder
WORKDIR /app
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

FROM python:3.12-slim
RUN groupadd -r app && useradd -r -g app app
COPY --from=builder /opt/venv /opt/venv
WORKDIR /app
COPY --chown=app:app . .
USER app
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
EXPOSE 8021
CMD ["gunicorn", "-c", "gunicorn.conf.py", "shelfspace.main:app"]
```

| Choice | Reason |
|---|---|
| **Multi-stage** | build tools never reach the final image |
| **`-slim`, not `alpine`** | musl breaks wheels; you end up compiling everything |
| **Non-root `USER`** | a container escape should not start as root |
| **`requirements.txt` copied first** | dependency layer cached across code changes |
| **`PYTHONUNBUFFERED=1`** | otherwise logs vanish when the container is killed |
| **Pinned base tag** (or a digest) | `python:3` changes under you |
| **`.dockerignore`** | keeps `.git`, `.venv`, tests out — smaller and safer |
| **No secrets in the image** | `ENV SECRET_KEY=…` is baked into a layer forever |

Scan the image (`trivy image shelfspace:latest`) in CI, and rebuild regularly —
most vulnerabilities in your image are in the base, and only a rebuild fixes them.

## 10. Gunicorn, workers and shutdown

```python
# gunicorn.conf.py
bind = "0.0.0.0:8021"
worker_class = "uvicorn.workers.UvicornWorker"
workers = int(os.getenv("WEB_CONCURRENCY", (os.cpu_count() or 1) * 2 + 1))
timeout = 60
graceful_timeout = 30
keepalive = 5
max_requests = 1000
max_requests_jitter = 100
accesslog = "-"
errorlog = "-"
```

- **Gunicorn supervises; Uvicorn serves.** Gunicorn restarts dead workers, which
  bare `uvicorn --workers` does not do as thoroughly. In Kubernetes, **one worker
  per container** is also a perfectly good answer — let the orchestrator scale
  and keep the process model simple.
- **`--reload` is never used in production** (Day 01).
- **`max_requests` with jitter** recycles workers, papering over slow leaks;
  jitter stops them all restarting at once.
- **`graceful_timeout` ≥ your slowest request**, and the orchestrator's
  termination grace period must be larger still, or in-flight requests become
  502s during every deploy.
- **Remember the multiplication** (Day 14): `workers × pool_size` connections
  against a PostgreSQL limit of 100. Four containers × 4 workers × 5 = 80.

```yaml
# Kubernetes: the two settings people forget
terminationGracePeriodSeconds: 45
lifecycle:
  preStop:
    exec: {command: ["sleep", "5"]}    # let the LB stop routing before we exit
```

## 11. CI, and the deploy sequence

```yaml
# .github/workflows/ci.yml (sketch)
jobs:
  test:
    services:
      postgres: {image: postgres:16, env: {POSTGRES_PASSWORD: postgres}, ...}
    steps:
      - run: pip install -r requirements.txt
      - run: ruff check . && ruff format --check .
      - run: mypy shelfspace
      - run: pytest --cov=shelfspace --cov-fail-under=80
      - run: pip-audit                      # known CVEs in dependencies
      - run: docker build -t shelfspace:${{ github.sha }} .
      - run: trivy image --exit-code 1 --severity HIGH,CRITICAL shelfspace:${{ github.sha }}
```

Test against **PostgreSQL, not SQLite** (Day 17). Tag images with the commit SHA,
never only `latest` — you cannot roll back to `latest`.

**The deploy sequence that avoids downtime:**

1. **Migrations run first**, as a separate step, and must be backward-compatible
   (Day 09): old code keeps running against the new schema during the rollout.
2. Roll out the new image gradually; readiness gates each replica.
3. Watch error rate and p99 for a few minutes.
4. Roll back by redeploying the previous SHA — which works only because step 1
   was additive.

Anything that must be removed happens in a **later** deploy, after no running
code references it. "Add column → deploy → backfill → constrain → (much later)
drop the old column" is four deploys and zero outages.

## 12. The production checklist

**Configuration & secrets**
- [ ] All config from the environment; validated at startup (Day 01, 07)
- [ ] `SECRET_KEY` generated with `secrets`, from a secret manager (Day 15)
- [ ] `.env` never committed; `.env.example` is
- [ ] Production settings refuse to start if unsafe (debug on, `*` CORS)

**Security**
- [ ] `/docs`, `/redoc`, `/openapi.json` disabled in production (Day 01)
- [ ] HTTPS enforced; HSTS; `nosniff`; `TrustedHost` (Day 13)
- [ ] CORS lists exact origins (Day 13)
- [ ] Rate limiting on everything, hardest on login (Day 13, 15)
- [ ] Passwords bcrypt/argon2, off the event loop (Day 15)
- [ ] Every route authorized; object-level checks in loaders (Day 16)
- [ ] Input and output models separate (Day 04, 05)
- [ ] Errors leak nothing (Day 06)
- [ ] Uploads validated by content, served from another origin (Day 19)
- [ ] `pip-audit` and image scanning in CI

**Data**
- [ ] Alembic migrations, backward-compatible, `downgrade` tested (Day 09)
- [ ] Constraints in the database, not only in Python (Day 09)
- [ ] Indexes on filtered/joined columns; N+1 tested (Day 11)
- [ ] Pool sized for `containers × workers` (Day 09, 14)
- [ ] **Backups taken, and a restore actually rehearsed**

**Runtime**
- [ ] Gunicorn + Uvicorn workers; no `--reload` (Day 21)
- [ ] Graceful shutdown longer than the slowest request
- [ ] No blocking calls in `async def` (Day 14)
- [ ] Timeouts on every outbound call (Day 14)
- [ ] Background work in a durable queue, handlers idempotent (Day 18)

**Observability**
- [ ] JSON logs to stdout with `request_id`, `version`, `env` (Day 13, 21)
- [ ] RED metrics with route-template labels
- [ ] `/health/live` and `/health/ready`, separate (Day 21)
- [ ] Alerts on error rate, p99, queue age, dead letters
- [ ] Tracing on the paths that matter
- [ ] An on-call runbook: what each alert means and what to do

## 13. Best practices introduced today

| Practice | Reason |
|---|---|
| JSON logs to stdout, never files | the platform collects and indexes them |
| `request_id`, `version`, `env` on every line | reconstructable, attributable |
| Never log secrets, bodies or PII | retention makes every leak permanent |
| RED metrics, labelled by route template | actual paths explode cardinality |
| Alert on symptoms, not CPU | users feel latency and errors |
| Liveness must not check dependencies | otherwise a hiccup restarts everything |
| Readiness checks dependencies, and is cached | probes are frequent |
| Multi-stage, slim, non-root images | small attack surface, small pulls |
| Pin the base image; rebuild regularly | your CVEs live in the base layer |
| No secrets in image layers | layers are forever and widely readable |
| `PYTHONUNBUFFERED=1` | otherwise logs are lost on a kill |
| Gunicorn + Uvicorn workers, or one worker per container | supervision, or orchestrator scaling |
| `graceful_timeout` > slowest request; grace period longer still | no 502s on deploy |
| `max_requests` with jitter | contains slow leaks, avoids stampedes |
| Size the pool for containers × workers | the database has a connection limit |
| Test on PostgreSQL in CI | SQLite hides real behaviour |
| Tag images with the commit SHA | you cannot roll back to `latest` |
| Migrations first, backward-compatible | old and new code overlap during rollout |
| Destructive schema changes in a later deploy | keeps rollback possible |
| Rehearse a restore | an untested backup is a hope |

## 14. Common mistakes

| Symptom | Cause | Fix |
|---|---|---|
| Cannot reconstruct a failure | unstructured logs, no request id | JSON + `request_id` |
| Logs lost when a container dies | buffered stdout | `PYTHONUNBUFFERED=1` |
| Disk full on the node | logging to files in the container | log to stdout |
| Metrics backend fell over | path used as a label | route template |
| Every container restarted at once | liveness probing the database | liveness checks nothing external |
| Traffic to a booting instance | no readiness probe | add one |
| Probes add measurable load | uncached readiness checks | cache for a few seconds |
| 502s during every deploy | grace period shorter than requests | raise it; add a preStop sleep |
| Rollback impossible | destructive migration shipped with the code | additive first |
| Rollback broke the schema | `downgrade` never tested | test it (Day 09) |
| `FATAL: too many connections` | workers × pool size | size for the total |
| 900 MB image | single-stage with build tools | multi-stage slim |
| Container runs as root | no `USER` | add one |
| Secret in the image | `ENV SECRET=` or a committed `.env` | secret manager |
| Green CI, broken production | SQLite in CI | PostgreSQL |
| Known CVEs shipped | no scanning | `pip-audit` + `trivy` |
| Alert fatigue | alerting on CPU and every warning | alert on user-visible symptoms |
| "Which version is running?" | version not in logs or `/health` | add it (Day 01) |
| Backup existed, restore failed | never rehearsed | rehearse it |

## 15. Exercises

1. Add the JSON formatter and confirm one `grep` of a request id returns the full
   story of that request across every log line.
2. Add RED metrics with route-template labels, then deliberately label by raw
   path and count the resulting series after 100 requests to different ids.
3. Implement `/health/live` and `/health/ready`, stop the database, and verify
   only readiness fails.
4. Write the Dockerfile, then compare image size and `docker history` against a
   naive single-stage build. Confirm `whoami` is not root.
5. Trigger a graceful shutdown while a 5-second request is in flight, and tune
   `graceful_timeout` until nobody sees a 502.
6. Set up CI with PostgreSQL, `ruff`, `mypy`, `pytest --cov-fail-under`,
   `pip-audit` and `trivy`. Make each one fail once, deliberately.
7. Do a zero-downtime column rename across four deploys, with rollback tested at
   each step.
8. Instrument with OpenTelemetry, find the slowest span on your slowest endpoint,
   and fix it.
9. Walk the section 12 checklist against your own app and write down every box
   you cannot tick.

## 16. You are done — and what comes next

Twenty-one days ago this was six lines returning `{"hello": "world"}`. It is now
a service with validated configuration, layered structure, a real database with
migrations, dependency injection, authentication and authorization, a consistent
error contract, background workers, realtime updates, a test suite, and the
observability to run it.

More importantly, you have the reasoning behind each of those, which is what
transfers to the next codebase.

Worth exploring next:

| Direction | Where to start |
|---|---|
| **Async all the way** | `AsyncSession` + `asyncpg`, converting one endpoint at a time (Day 14) |
| **Caching** | Redis, cache invalidation, `Cache-Control` and ETags done properly (Day 12) |
| **Full-text search** | PostgreSQL `tsvector`, then OpenSearch when you outgrow it (Day 11) |
| **Event-driven architecture** | the outbox pattern from Day 18, Kafka/NATS, eventual consistency |
| **GraphQL alongside REST** | Strawberry with FastAPI — and the N+1 problem returns (Day 11) |
| **Multi-tenancy** | row-level security, per-tenant schemas, and their trade-offs |
| **Load testing** | `locust` or `k6` against the metrics from today |
| **Chaos and failure drills** | kill the database during a deploy and see what your checklist missed |

Re-read Day 01 now. Everything in it will look like an obvious consequence of
something you learned later — which is the clearest sign the twenty-one days
worked.

**[← Back to the course index](../README.md)**
