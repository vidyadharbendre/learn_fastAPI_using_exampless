# Day 14 — Async and Concurrency

> **Goal:** understand what `async def` actually does — the event loop, the one
> mistake that freezes your entire server, when `def` is the *better* choice, and
> how to run I/O concurrently instead of in sequence.
> **Time:** ~2.5 hours · **Port:** 8014 · **Builds on:** Day 13

> **Code status:** the README is the spec. Build `shelfspace/` yourself from
> sections 5 onwards before reaching for a reference implementation.

---

## 1. Why this matters

> **One blocking call inside `async def` does not slow that request. It stops
> every request in the process.**

This is the single most common serious FastAPI bug, and it is invisible in
testing: with one user, a blocking call is merely slow. With fifty concurrent
users, the server appears to hang, health checks time out, the load balancer
removes the instance, and the traffic moves to the next instance, which then does
the same thing.

Copying `async def` from a tutorial without understanding it is how you get
there. Today removes the mystery.

## 2. What you will build

```
14_async_and_concurrency/
├── run.py
└── shelfspace/
    ├── clients/
    │   ├── pricing.py       an async HTTP client, shared via lifespan
    │   └── legacy.py        a deliberately blocking client
    ├── api/v1/enrich.py     sequential vs gather, blocking vs threadpool
    ├── bench.py             a load generator to prove every claim
    └── main.py
```

## 3. Run it

```bash
source .venv/bin/activate
cd 14_async_and_concurrency
python run.py
```

## 4. Try it — learn by doing

```bash
API=http://127.0.0.1:8014/api/v1

# --- ONE request each. All three look fine. ---
curl -s -o /dev/null -w 'async-sleep  %{time_total}s\n' "$API/demo/async-sleep?seconds=1"
curl -s -o /dev/null -w 'sync-def     %{time_total}s\n' "$API/demo/sync-def?seconds=1"
curl -s -o /dev/null -w 'BLOCKING     %{time_total}s\n' "$API/demo/blocking-in-async?seconds=1"

# --- now TEN at once. This is the whole day. ---
time (for i in $(seq 1 10); do curl -s -o /dev/null "$API/demo/async-sleep?seconds=1" & done; wait)
time (for i in $(seq 1 10); do curl -s -o /dev/null "$API/demo/sync-def?seconds=1" & done; wait)
time (for i in $(seq 1 10); do curl -s -o /dev/null "$API/demo/blocking-in-async?seconds=1" & done; wait)
#   async     ≈ 1s   — concurrent
#   sync def  ≈ 1-2s — thread pool, concurrent up to its size
#   blocking  ≈ 10s  — SERIALISED, and nothing else runs either

# --- proof that a blocking endpoint freezes UNRELATED requests ---
curl -s "$API/demo/blocking-in-async?seconds=5" &   # start the freeze
sleep 0.3
time curl -s -o /dev/null $API/books               # a fast endpoint... is not

# --- sequential vs concurrent I/O in one handler ---
curl -s -o /dev/null -w 'sequential %{time_total}s\n' "$API/books/1/enrich?mode=sequential"
curl -s -o /dev/null -w 'gather     %{time_total}s\n' "$API/books/1/enrich?mode=gather"

# --- the thread pool has a size, and it is a limit ---
time (for i in $(seq 1 60); do curl -s -o /dev/null "$API/demo/sync-def?seconds=1" & done; wait)

# --- CPU work blocks the loop exactly like I/O does ---
curl -s -o /dev/null -w 'cpu-in-async %{time_total}s\n' "$API/demo/cpu?n=8000000"
curl -s -o /dev/null -w 'cpu-in-pool  %{time_total}s\n' "$API/demo/cpu-process?n=8000000"

# --- timeouts: an upstream that never answers must not hold your worker ---
curl -s $API/demo/upstream-timeout | python -m json.tool     # 504, after 2s
```

Run the three `time (...)` loops and keep the numbers. Everything below explains
them.

## 5. The event loop, in one paragraph

A FastAPI process runs **one** event loop on **one** thread. The loop holds a
queue of ready tasks and runs them one at a time. When a task hits `await` on
something that is not ready — a socket, a timer — it **yields**, and the loop
runs another task while waiting. Concurrency comes entirely from tasks yielding.

Two consequences follow, and they are the whole model:

- **`await` is a yield point.** Between two `await`s, your code runs
  uninterrupted.
- **Code that never yields never lets anything else run.** `time.sleep(5)`,
  `requests.get()`, a 200 ms CPU loop — none of them yield. During that time the
  loop is stopped: no other request progresses, no health check is answered, no
  timer fires.

That is the difference between "this request is slow" and "the server is down".

## 6. `def` vs `async def` in FastAPI

FastAPI accepts both and treats them differently:

| You write | FastAPI runs it | Concurrency limit |
|---|---|---|
| `async def` | on the event loop | thousands, if it yields |
| `def` | in a **thread pool** (AnyIO, 40 threads by default) | the pool size |

So a plain `def` endpoint that blocks is *fine* — it blocks a worker thread, not
the loop. This is the rule to remember:

> **If any call in the function blocks and you cannot `await` it, make the
> endpoint `def`. If everything I/O-bound is awaitable, make it `async def`.**

```python
# ✅ everything awaits
async def get_price(client: HTTPDep) -> Price:
    r = await client.get("https://pricing.internal/price")
    return Price(**r.json())

# ✅ blocking library, plain def → runs in the thread pool
def get_price_legacy() -> Price:
    r = requests.get("https://pricing.internal/price", timeout=2)
    return Price(**r.json())

# ❌ the bug: blocking call inside async def
async def get_price_broken() -> Price:
    r = requests.get("https://pricing.internal/price")     # freezes everything
    return Price(**r.json())
```

**Never mark a function `async` just because it looks modern.** An `async def`
that contains no `await` is strictly worse than `def`: it gains nothing and it
loses the thread-pool safety net.

The same rule applies to dependencies (Day 08) and to `BackgroundTasks` (Day 18)
— both respect `def` vs `async def` the same way.

## 7. The blocking calls people miss

| Blocking | Async alternative |
|---|---|
| `time.sleep()` | `await asyncio.sleep()` |
| `requests.get()` | `await httpx.AsyncClient().get()` |
| sync SQLAlchemy `Session` | `AsyncSession` + `asyncpg`, or a `def` endpoint |
| `open().read()` on a big file | `anyio.to_thread.run_sync`, or `aiofiles` |
| `subprocess.run()` | `await asyncio.create_subprocess_exec()` |
| `redis.Redis()` | `redis.asyncio.Redis()` |
| `boto3` | `aioboto3`, or a thread |
| `bcrypt.hashpw()` (CPU, ~100 ms **by design**) | thread pool — see Day 15 |
| `smtplib` | a background worker (Day 18) |
| a tight CPU loop, big JSON, image resize | `run_in_executor` / a process pool |

Password hashing deserves the callout: bcrypt/argon2 are *deliberately* slow, so
verifying a password inside `async def` stalls the loop for ~100 ms per login. At
20 logins/second your API is effectively down.

To run one blocking call inside an otherwise async handler:

```python
from anyio import to_thread
result = await to_thread.run_sync(blocking_function, arg)     # off the loop
```

For **CPU-bound** work, threads do not help — the GIL still serialises them. Use
a process pool, or better, move the work to a queue (Day 18):

```python
loop = asyncio.get_running_loop()
result = await loop.run_in_executor(process_pool, cpu_heavy, data)
```

## 8. Actually being concurrent

`async` alone buys nothing if you await one thing after another:

```python
# ❌ sequential: 300 ms
price   = await pricing.get(book.isbn)          # 100 ms
reviews = await reviews_api.get(book.id)        # 100 ms
stock   = await warehouse.get(book.isbn)        # 100 ms

# ✅ concurrent: ~100 ms
price, reviews, stock = await asyncio.gather(
    pricing.get(book.isbn),
    reviews_api.get(book.id),
    warehouse.get(book.isbn),
)
```

`gather` is the workhorse. Two options that matter:

```python
results = await asyncio.gather(*calls, return_exceptions=True)  # partial success
for r in results:
    if isinstance(r, Exception): ...        # degrade instead of failing the request
```

Without `return_exceptions=True`, the first failure propagates and the others are
cancelled — right for "all or nothing", wrong for "show what we could get".

**Structured concurrency** (`anyio.create_task_group`, or `asyncio.TaskGroup` on
3.11+) is the modern form and cleans up better on failure:

```python
async with asyncio.TaskGroup() as tg:
    t1 = tg.create_task(pricing.get(isbn))
    t2 = tg.create_task(reviews_api.get(book_id))
# both are guaranteed finished or cancelled here
```

For fan-out over many items, bound it — 500 concurrent upstream calls will get
you rate-limited or exhaust file descriptors:

```python
sem = asyncio.Semaphore(10)
async def fetch(isbn):
    async with sem:
        return await pricing.get(isbn)
```

## 9. Timeouts and cancellation

> **Every network call needs a timeout. Without one, a hung upstream holds your
> connection until something else gives up first — usually your user.**

```python
async with asyncio.timeout(2.0):                 # 3.11+
    return await pricing.get(isbn)

# or at the client, which is better — it applies everywhere
httpx.AsyncClient(timeout=httpx.Timeout(2.0, connect=1.0))
```

When a timeout fires, asyncio raises `CancelledError` inside the task.
**Never swallow it:**

```python
try:
    ...
except asyncio.CancelledError:
    cleanup()
    raise                      # ALWAYS re-raise
except Exception:
    ...
```

A bare `except Exception` does not catch `CancelledError` in modern Python (it
inherits from `BaseException`) — which is exactly why bare `except:` is dangerous
here.

Also note **clients disconnect**. If a user closes the tab, the request may be
cancelled mid-handler, potentially between two writes. Anything that must
complete regardless belongs in a background task or a queue, not in the request
path.

## 10. Shared clients, not per-request ones

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http = httpx.AsyncClient(
        timeout=httpx.Timeout(5.0, connect=2.0),
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
    )
    yield
    await app.state.http.aclose()

def get_http(request: Request) -> httpx.AsyncClient:
    return request.app.state.http
```

Creating an `AsyncClient` per request throws away connection pooling and TLS
session reuse — often doubling latency — and leaks sockets if you forget to close
it. One client per process, created in `lifespan`, injected by a trivial
dependency (Day 08).

The same applies to Redis clients and async database engines.

## 11. Workers, threads, and where the limits actually are

```bash
uvicorn shelfspace.main:app --workers 4          # 4 processes, 4 event loops
gunicorn -k uvicorn.workers.UvicornWorker -w 4   # production (Day 21)
```

| Knob | What it bounds |
|---|---|
| worker processes | true CPU parallelism (the GIL is per process) |
| the event loop | concurrent *awaiting* tasks — thousands |
| AnyIO thread pool (40) | concurrent `def` endpoints and `to_thread` calls |
| DB pool size (Day 09) | concurrent queries **per process** |

These interact in ways that surprise people: 4 workers × `pool_size=5` is 20
database connections, and PostgreSQL's default limit is 100. Meanwhile the thread
pool being full does not raise anything — requests just queue silently, and your
p99 goes up with no error to point at.

Rule of thumb: `workers = CPU cores` for CPU-bound work, and 2–4× cores for
I/O-bound work, then **measure**. There is no correct number derivable from first
principles.

## 12. Measuring, not guessing

```python
# bench.py
async def hammer(url: str, n: int, concurrency: int) -> None:
    sem = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient() as client:
        async def one():
            async with sem:
                t = time.perf_counter()
                await client.get(url)
                return time.perf_counter() - t
        times = await asyncio.gather(*(one() for _ in range(n)))
    times.sort()
    print(f"p50 {times[n//2]*1000:.0f}ms  p95 {times[int(n*.95)]*1000:.0f}ms")
```

Report **p95 and p99**, not the mean. A blocked event loop barely moves the mean
and destroys the tail — which is what users actually experience.

The quickest diagnostic for "is my loop blocked": run a heartbeat task that logs
if a `0.1 s` sleep takes materially longer.

```python
async def loop_lag_monitor():
    while True:
        t = time.perf_counter()
        await asyncio.sleep(0.1)
        lag = time.perf_counter() - t - 0.1
        if lag > 0.05:
            logger.warning("event loop lag %.3fs", lag)
```

That single task will find your blocking call faster than any amount of reading.

## 13. Best practices introduced today

| Practice | Reason |
|---|---|
| `async def` only when everything I/O-bound is awaited | otherwise the loop stalls |
| `def` for any blocking library call | the thread pool absorbs it |
| Never `async def` without an `await` | strictly worse than `def` |
| `httpx` over `requests` in async code | `requests` cannot yield |
| `asyncio.gather` for independent calls | latency becomes the max, not the sum |
| `return_exceptions=True` for partial success | one flaky upstream should not fail everything |
| `TaskGroup` / task groups for structured concurrency | guaranteed cleanup on failure |
| Bound fan-out with a semaphore | 500 concurrent calls is an outage upstream |
| A timeout on **every** network call | a hung upstream must not hold your worker |
| Re-raise `CancelledError` | swallowing it breaks timeouts and shutdown |
| One shared `AsyncClient` per process, via `lifespan` | pooling and TLS reuse |
| Hash passwords in a thread | bcrypt is CPU-bound by design |
| CPU work to a process pool or a queue | threads cannot beat the GIL |
| Know your four limits (workers, loop, threads, DB pool) | they interact |
| Measure p95/p99 under concurrency | the mean hides a blocked loop |
| Run an event-loop lag monitor | it finds blocking calls for you |

## 14. Common mistakes

| Symptom | Cause | Fix |
|---|---|---|
| Server hangs under load; fine with one user | blocking call in `async def` | `def`, or await properly |
| Health checks time out under load | same — the loop cannot answer | same |
| p50 fine, p99 terrible | intermittent loop blocking | lag monitor |
| `async` endpoint no faster | awaits in sequence | `gather` |
| `requests` used in an async handler | not awaitable | `httpx.AsyncClient` |
| Latency doubled per call | new `AsyncClient` per request | share one via `lifespan` |
| Sockets exhausted | clients never closed | `lifespan` teardown |
| Logins stall the API | bcrypt on the event loop | `to_thread.run_sync` |
| CPU work in a thread does not help | the GIL | process pool or a queue |
| Requests queue with no errors | thread pool full | fewer blocking endpoints |
| Timeout fires but the task keeps running | `CancelledError` swallowed | re-raise |
| Shutdown hangs | tasks ignoring cancellation | cooperate with cancellation |
| `RuntimeError: no running event loop` | async call from sync code | `asyncio.run`, or make the caller async |
| `Session is already flushing` / weird ORM errors | sync session shared across tasks | one session per request |
| Too many DB connections | workers × pool size | size the pool for the total |
| Half-written data when a user closed the tab | request cancelled mid-handler | background task or queue |

## 15. Exercises

1. Build the three demo endpoints and run all three `time (...)` loops in
   section 4. Write down the numbers; they are the day's evidence.
2. Start `/demo/blocking-in-async?seconds=5`, then time `/books` in another
   terminal. Explain the result to someone else in two sentences.
3. Convert `/books/1/enrich` from sequential to `gather` and measure. Then make
   one upstream fail and add `return_exceptions=True` for graceful degradation.
4. Add the event-loop lag monitor, deliberately block the loop, and confirm the
   warning fires.
5. Move a blocking legacy client into `to_thread.run_sync` and re-run the
   concurrency test.
6. Add a 2-second timeout that returns your Day 06 envelope with `504`. Confirm
   `CancelledError` is re-raised and nothing leaks.
7. Compare an `AsyncClient` per request against one shared via `lifespan` at
   concurrency 50. Explain the difference in p95.
8. Run `--workers 1` and `--workers 4` against the CPU endpoint and against the
   async endpoint. Explain why one improves and the other does not.

## 16. What's next

**[Day 15 — Authentication with JWT →](../15_authentication_with_jwt/)**
Every endpoint so far is wide open. Tomorrow: password hashing done properly
(off the event loop, as of today), JWT access and refresh tokens, and the
dependency that turns a header into a `User`.
