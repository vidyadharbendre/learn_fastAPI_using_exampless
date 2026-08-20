# Day 01 — Environment Setup and Your First API

> **Goal:** get a *real* FastAPI service running — isolated environment,
> validated settings, an application factory, and a `/health` endpoint a load
> balancer would accept.
> **Time:** ~2 hours · **Port:** 8001 · **Builds on:** nothing

---

## 1. Why this matters

> **Every FastAPI tutorial starts with six lines. Every FastAPI *outage* starts
> with what those six lines left out.**

```python
from fastapi import FastAPI
app = FastAPI()

@app.get("/")
def root():
    return {"hello": "world"}
```

That runs. It is also unconfigurable, untestable, has no version anyone can
query, no way to know whether it is healthy, and publishes its interactive docs
to the internet the day you deploy it.

Today costs an extra ninety lines and removes all five problems. The lesson is
not "how do I start a server" — it is **what a service needs on the day it is
born**, because retrofitting any of it later means touching every file.

## 2. What you will build

**Shelfspace** — the bookstore API you extend for the next twenty days.

```
01_environment_setup_and_first_api/
├── run.py              python run.py → server on :8001
├── .env.example        every setting, documented
└── shelfspace/
    ├── __init__.py     __version__ — one source of truth
    ├── config.py       Settings: environment → validated Python objects
    ├── schemas.py      response contracts (Pydantic)
    ├── data.py         the catalogue (a list today, Postgres on Day 09)
    └── main.py         create_app(), lifespan, three endpoints
```

Three endpoints: `GET /` (discovery), `GET /health` (liveness + version),
`GET /books` (the catalogue).

## 3. Set up the environment

A virtual environment is not ceremony. Installing into the system Python mixes
your project's dependencies with your operating system's, and on macOS with
Homebrew Python it now simply refuses (`externally-managed-environment`).

```bash
# from the repository root, once for all 21 days
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Confirm you are in it — the answer must contain `.venv`:

```bash
which python                       # Windows: where python
python -c "import fastapi; print(fastapi.__version__)"
```

| Command | What it actually does |
|---|---|
| `python3 -m venv .venv` | creates a private interpreter + `site-packages` |
| `source .venv/bin/activate` | puts `.venv/bin` first on `$PATH` |
| `pip install -r requirements.txt` | installs **pinned** versions, so your run matches mine |
| `deactivate` | restores the old `$PATH` |

`.venv/` is in `.gitignore`. You commit `requirements.txt`, never the
environment — it is hundreds of megabytes and platform-specific.

## 4. Run it

```bash
source .venv/bin/activate
cd 01_environment_setup_and_first_api

python run.py
```

Equivalent, if you prefer the explicit form:

```bash
uvicorn shelfspace.main:app --reload --port 8001
```

Read that target: `shelfspace.main` is the **module**, `app` is the **object**
inside it, separated by a colon. Getting this wrong is the single most common
first-day error, and the message (`Could not import module`) tells you almost
nothing.

Now open the two URLs that make FastAPI worth using:

- <http://127.0.0.1:8001/docs> — Swagger UI, **executable**: click *Try it out*
- <http://127.0.0.1:8001/redoc> — ReDoc, better for reading

You wrote no documentation. Both pages are generated from your type hints, and
they cannot drift from the code because they *are* the code.

## 5. Try it — learn by doing

```bash
API=http://127.0.0.1:8001

# --- the three endpoints ---
curl -s $API/        | python -m json.tool
curl -s $API/health  | python -m json.tool
curl -s $API/books   | python -m json.tool

# --- the machine-readable contract behind /docs ---
curl -s $API/openapi.json | python -m json.tool | head -40

# --- what the framework does for free ---
curl -si $API/no-such-endpoint | head -8      # 404, as JSON — not an HTML page
curl -sX POST $API/books       | python -m json.tool   # 405: path exists, verb doesn't
curl -sI $API/health           | head -5      # HEAD: headers, no body

# --- see the reloader work ---
# edit shelfspace/main.py, save, watch the terminal restart, then:
curl -s $API/health | python -m json.tool     # uptime_seconds is back to ~0
```

Two things worth pausing on:

**`uptime_seconds` resets on reload.** That is the app process restarting — proof
that `--reload` really does rebuild everything, and a hint about why it belongs
nowhere near production.

**The 404 body is JSON.** A framework that returns an HTML error page to a JSON
client has broken the contract at the worst possible moment. FastAPI never does.

## 6. `create_app()` — why a factory, not a global

```python
app = FastAPI()                    # ❌ configured exactly once, forever
def create_app(settings=None):     # ✅ build one per configuration
    ...
    return app
```

A module-level `app` is created at **import time**, with whatever the
environment happened to hold. That makes one thing impossible: building a second
app, configured differently, inside a test — which is exactly what
`test_docs_are_disabled_in_production` needs.

The bottom of `main.py` still exposes `app = create_app()`, because Uvicorn needs
an object to point at. That is the *default* app, not the only possible one.

## 7. Lifespan — startup and shutdown in one place

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    ...            # runs ONCE, before the first request is accepted
    yield
    ...            # runs on shutdown
```

Everything before `yield` runs at startup; everything after runs at shutdown.
From Day 09 this is where the database pool is opened and closed, and from Day 14
where a shared `httpx.AsyncClient` lives.

Doing that work at import time instead — a module-level `engine = create_engine(...)`
— means importing the module opens a socket. Your test suite then needs a live
database to *collect* tests, and `--reload` leaks a connection on every save.

> `@app.on_event("startup")` is the old spelling. It is deprecated; `lifespan`
> replaced it because a single context manager can hold state between the two
> halves, and an event pair cannot.

## 8. Settings — parse the environment once, at the edge

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="SHELFSPACE_")
    port: int = 8001
    environment: str = "development"
```

| Approach | What happens when `PORT="8OO1"` (letter O) |
|---|---|
| `os.getenv("PORT")` | a **string** flows on; something 200 lines away fails |
| `int(os.getenv("PORT"))` | `ValueError` at first use, mid-request |
| `Settings()` | `ValidationError` **at startup**; the container never goes live |

The third is what you want. A deploy that refuses to start is a page to on-call;
a deploy that starts and misbehaves is a mystery incident three days later.

**Precedence**, highest first: real environment variables → `.env` file →
defaults in the class. So a container's env always wins over a stray `.env`.

**`env_prefix`** namespaces your variables. Without it, a setting called `port`
or `debug` collides with something else in a shared shell — with it, only
`SHELFSPACE_PORT` is yours.

**`@lru_cache`** means the environment is parsed once per process rather than
per request, and gives Day 08 a clean dependency to override in tests.

**`.env` is gitignored; `.env.example` is committed.** The example documents
which settings exist without leaking a single real value.

## 9. Type hints are not decoration — they are the contract

```python
@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
```

One annotation buys four things at once:

| | |
|---|---|
| **Validation** | the outgoing payload is checked against the model |
| **Filtering** | fields not declared on the model are **stripped**, not sent |
| **Documentation** | `/docs` and `/openapi.json` are generated from it |
| **Editor support** | real autocomplete and type errors before you run |

The filtering is the one people underestimate. Add `"password_hash": ...` to a
returned dict and `response_model` silently drops it. That is a leak that never
happens — and it is why the test asserting the exact key set matters.

## 10. `async def` or `def`?

Both work. Today's endpoints are `async def` and do no I/O, which is fine.

| You write | FastAPI runs it |
|---|---|
| `async def` | directly on the event loop |
| `def` | in a **thread pool**, so it cannot block the loop |

The rule that matters, and the reason Day 14 exists: **an `async def` endpoint
that calls blocking code stops the entire server** — every other request, not
just this one. A plain `def` endpoint doing the same thing is merely slow.

```python
async def bad():
    time.sleep(5)                 # ❌ freezes the whole process for 5s
    requests.get(url)             # ❌ same — `requests` is blocking

async def good():
    await asyncio.sleep(5)        # ✅ yields to the loop
    await client.get(url)         # ✅ httpx is async-aware
```

If you are unsure whether a library is async-aware, use `def`. The thread pool is
slower than a proper coroutine and dramatically faster than a stalled event loop.

## 11. `/health` — the endpoint every service needs on day one

```json
{
  "status": "ok",
  "service": "Shelfspace API",
  "version": "0.1.0",
  "environment": "development",
  "uptime_seconds": 12.4,
  "checked_at": "2026-08-20T09:15:00Z"
}
```

| Field | Who reads it |
|---|---|
| `status` | the load balancer, deciding whether to send you traffic |
| `version` | **you, at 3 a.m.**, establishing which build is actually running |
| `environment` | you, confirming staging config did not ship to production |
| `uptime_seconds` | you, spotting a container in a crash-restart loop |

Two rules learned the hard way:

- **A health check must not check your dependencies.** If `/health` queries the
  database, a slow database makes every replica report unhealthy, the load
  balancer removes them all, and a degradation becomes an outage. Keep liveness
  ("this process runs") separate from readiness ("its dependencies answer").
- **`time.monotonic()`, never `time.time()`.** Wall-clock time jumps when NTP
  corrects it; monotonic time cannot go backwards. Uptime computed from
  `time.time()` can come out negative.

## 12. Money as a string, timestamps with an offset

Two conventions this course holds from the first file:

```python
price: str          # "499.00", not 499.00
checked_at: datetime = datetime.now(timezone.utc)
```

1. **JSON has one numeric type** — an IEEE-754 double. `12.10` can arrive as
   `12.099999999999999`, and financial totals drift by cents. Send money as a
   string, parse it into `Decimal`. Day 12 revisits this.
2. **`datetime.now()` returns a naive timestamp** with no offset, so the client
   guesses the zone — usually its own, usually wrong by hours.
   `datetime.now(timezone.utc)` serialises with a `Z`/`+00:00` the client can
   trust. There is a test asserting exactly this.

## 13. Disable `/docs` in production

```python
docs_url=None if settings.is_production else "/docs",
openapi_url=None if settings.is_production else "/openapi.json",
```

`/docs` is a development tool that publishes every route, every parameter and
every schema — an attacker's reconnaissance phase, pre-completed and hosted by
you. `/openapi.json` must go with it, or the schema is still readable by anyone
who guesses the path.

## 14. Best practices introduced today

| Practice | Reason |
|---|---|
| One `.venv`, never system Python | dependency isolation; modern Python enforces it |
| Pin versions in `requirements.txt` | your run and mine are the same run |
| Commit `requirements.txt`, ignore `.venv/` | huge, platform-specific, regenerable |
| Application factory, not a global `app` | tests need differently-configured apps |
| `lifespan`, not import-time side effects | importing a module must not open sockets |
| Settings as a validated class | fail at startup, not mid-request |
| Namespace env vars with a prefix | avoids collisions in a shared environment |
| Commit `.env.example`, ignore `.env` | document the knobs, leak no secrets |
| `@lru_cache` on `get_settings()` | parse once per process; overridable in tests |
| `response_model` on every endpoint | validates, filters, documents |
| `/health` from the first commit | deployment tooling needs it before you do |
| Report `version` in `/health` | the first question in every incident |
| Liveness ≠ readiness | a slow dependency must not empty the pool |
| `time.monotonic()` for durations | wall-clock time can move backwards |
| Money as a string | JSON floats lose precision |
| Timezone-aware UTC timestamps | naive timestamps are ambiguous |
| Envelope collections, never bare arrays | leaves room for pagination later |
| `--reload` in development only | it doubles processes and memory for nothing |
| No `/docs` in production | it publishes your entire attack surface |
| A discoverable root document | `curl` becomes the documentation |

## 15. Common mistakes

| Symptom | Cause | Fix |
|---|---|---|
| `command not found: uvicorn` | venv not activated | `source .venv/bin/activate` |
| `ModuleNotFoundError: fastapi` | installed into system Python | activate, then reinstall |
| `Could not import module "main"` | wrong target string | `uvicorn shelfspace.main:app` — `module:object` |
| `Error loading ASGI app. Attribute "app" not found` | object name is wrong | it must match the variable in the module |
| `[Errno 48] Address already in use` | old server still running | `lsof -ti:8001 \| xargs kill` |
| Edits do nothing | no `--reload` | `python run.py`, or add the flag |
| Reload loops forever | it is watching generated files | narrow with `--reload-dir` |
| `422` on a request you think is fine | validation, not a bug | read `detail` — it names the field and the reason |
| Server hangs under load | blocking call in `async def` | `await` it, or make the endpoint `def` |
| Timestamps off by hours for clients | `datetime.now()` is naive | `datetime.now(timezone.utc)` |
| Totals drift by a cent | money sent as a JSON number | send a string, parse to `Decimal` |
| Secret leaked in the response | returned a raw dict | declare `response_model`; it strips extras |
| `.env` committed | not in `.gitignore` | ignore it, commit `.env.example` |
| Works locally, fails in CI | unpinned versions | pin them |
| Prod config leaked to a test | module-level `app` | use `create_app(settings)` |
| Whole cluster marked unhealthy | `/health` queries the database | split liveness from readiness |
| `--reload` in production | copied the dev command | `gunicorn -k uvicorn.workers.UvicornWorker` (Day 21) |

## 16. Exercises

1. Add `GET /books/{book_id}` returning one book, and a proper `404` when the id
   does not exist. (Day 02 does this properly — try it yourself first.)
2. Add a `SHELFSPACE_MAX_PAGE_SIZE: int = 100` setting and expose it on `/`.
   Then set `SHELFSPACE_MAX_PAGE_SIZE=abc` and watch *where* it fails.
3. Add `GET /ready` that reports readiness separately from `/health`, and write
   down which one your load balancer should poll.
4. Set `SHELFSPACE_ENVIRONMENT=production` and confirm `/docs` returns 404 —
   then confirm `/openapi.json` does too.
5. Add a `request_count` that `/health` reports. Notice you need somewhere to
   put shared state — that is what `lifespan` is for.
6. Break the contract on purpose: return `{"status": "ok"}` from `/health` and
   read the 500 that `response_model` produces. Response validation is
   *server-side*, and it fails loudly rather than shipping a broken payload.
7. Run `pytest -m day01 -v` and read every test name as a sentence. They are a
   specification of today's behaviour.

## 17. What's next

**[Day 02 — HTTP Methods and Routing →](../02_http_methods_and_routing/)**
Today the catalogue is read-only. Tomorrow it accepts writes: `POST`, `PUT`,
`PATCH` and `DELETE`, the status code each one owes the client, and why the
order you declare your routes in changes which one runs.
