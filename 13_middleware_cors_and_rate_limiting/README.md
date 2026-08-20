# Day 13 — Middleware, CORS and Rate Limiting

> **Goal:** work on the layer *around* every endpoint — request IDs, timing,
> compression, the CORS headers browsers demand, and a rate limiter that stops
> one client consuming the whole service.
> **Time:** ~2.5 hours · **Port:** 8013 · **Builds on:** Day 12

> **Code status:** the README is the spec. Build `shelfspace/` yourself from
> sections 5 onwards before reaching for a reference implementation.

---

## 1. Why this matters

> **Some things must happen for every request, including the ones that fail
> before your code runs.**

A request ID that only exists inside successful handlers is useless in exactly
the situation you need it. A timing metric collected in the endpoint misses the
time spent parsing the body. A rate limiter implemented as a dependency runs
after validation — so a flood of malformed requests still costs you full
validation on every one.

Middleware is the layer that wraps everything. It is also the layer where a
five-line mistake makes every response slow, or quietly disables CORS for the
whole API.

## 2. What you will build

```
13_middleware_cors_and_rate_limiting/
├── run.py
└── shelfspace/
    ├── middleware/
    │   ├── request_id.py     generate/propagate X-Request-ID
    │   ├── timing.py         X-Process-Time + a slow-request log
    │   ├── logging.py        one structured line per request
    │   ├── body_limit.py     reject oversized bodies early
    │   └── security.py       nosniff, frame-options, HSTS
    ├── limiter.py            token-bucket rate limiting + 429 + Retry-After
    └── main.py               where the ORDER of the stack is decided
```

## 3. Run it

```bash
source .venv/bin/activate
cd 13_middleware_cors_and_rate_limiting
python run.py
```

## 4. Try it — learn by doing

```bash
API=http://127.0.0.1:8013/api/v1

# --- every response carries an id and a duration ---
curl -sI $API/books | grep -iE 'x-request-id|x-process-time'

# --- a client-supplied id is PROPAGATED, not replaced ---
curl -sI $API/books -H 'X-Request-ID: my-trace-123' | grep -i x-request-id

# --- and it appears in the error envelope AND the log line ---
curl -s $API/books/9999 -H 'X-Request-ID: my-trace-123' | python -m json.tool
# now grep your server output for my-trace-123 — request log + error, same id

# --- compression, but only above the minimum size ---
curl -s -H 'Accept-Encoding: gzip' -o /dev/null -w 'gzip:  %{size_download}\n' "$API/books?per_page=100"
curl -s                            -o /dev/null -w 'plain: %{size_download}\n' "$API/books?per_page=100"

# --- CORS: a preflight, then a real request ---
curl -si -X OPTIONS $API/books \
  -H 'Origin: http://localhost:3000' \
  -H 'Access-Control-Request-Method: POST' \
  -H 'Access-Control-Request-Headers: content-type,authorization' | head -12

curl -si $API/books -H 'Origin: http://localhost:3000'      | grep -i access-control
curl -si $API/books -H 'Origin: http://evil.example'        | grep -i access-control  # nothing

# --- rate limiting: watch the headers count down, then the 429 ---
for i in $(seq 1 12); do
  curl -s -o /dev/null -w "%{http_code} " $API/books -H 'X-API-Key: demo'
done; echo
curl -si $API/books -H 'X-API-Key: demo' | grep -iE 'x-ratelimit|retry-after'

# --- oversized bodies are rejected BEFORE they are read into memory ---
python -c "print('{\"title\":\"' + 'x'*2_000_000 + '\"}')" > /tmp/big.json
curl -s -o /dev/null -w '%{http_code}\n' -X POST $API/books \
  -H 'Content-Type: application/json' --data-binary @/tmp/big.json      # 413

# --- security headers on every response ---
curl -sI $API/books | grep -iE 'x-content-type-options|x-frame-options'

# --- slow requests are logged as such ---
curl -s "$API/debug/slow?seconds=2" -o /dev/null   # check the log for a WARNING
```

## 5. Two ways to write middleware

**The decorator** — quick, readable, fine for most things:

```python
@app.middleware("http")
async def add_process_time(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Process-Time"] = f"{time.perf_counter() - start:.4f}"
    return response
```

**The `BaseHTTPMiddleware` class** — when it needs configuration or reuse:

```python
class TimingMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, slow_threshold_ms: int = 500):
        super().__init__(app)
        self.slow_threshold_ms = slow_threshold_ms

    async def dispatch(self, request: Request, call_next):
        ...

app.add_middleware(TimingMiddleware, slow_threshold_ms=300)
```

Both are the same mechanism; the decorator registers a `BaseHTTPMiddleware`
instance for you.

**Pure ASGI middleware** is the third option, and the one to use when performance
matters or you need to touch the raw protocol:

```python
class ASGIRequestID:
    def __init__(self, app): self.app = app
    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        ...
```

`BaseHTTPMiddleware` is convenient and carries real costs: it buffers the
response (breaking streaming, Day 19), adds a task per request, and historically
has awkward interactions with background tasks and exception propagation. Use it
for ordinary header/logging work; drop to ASGI for anything hot or streaming.

## 6. Order is the part people get wrong

```python
app.add_middleware(RequestIDMiddleware)      # added LAST  → runs FIRST
app.add_middleware(TimingMiddleware)
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins)
```

> **Middleware added last runs first.** The stack wraps outward-in on the way
> down and inward-out on the way back.

```
request  → RequestID → Timing → GZip → CORS → router → endpoint
response ← RequestID ← Timing ← GZip ← CORS ← router ← endpoint
```

Consequences worth internalising:

- **Request ID must be outermost**, so every other layer — including error
  handling — can read it.
- **Timing sits just inside it**, so it measures everything below.
- **CORS must be outermost enough to run on error responses too.** A `500`
  without CORS headers appears in the browser as a CORS error, and you spend an
  hour debugging the wrong thing.
- **GZip before anything that reads the body bytes** downstream, or it reads
  compressed bytes.

Exception handlers (Day 06) run *inside* the middleware stack, at the app level.
An exception raised in middleware itself does **not** hit them — so middleware
must handle its own errors or it returns a bare, envelope-less 500.

## 7. Request IDs: the thread through everything

```python
request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")

class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        rid = request.headers.get("X-Request-ID") or uuid4().hex[:16]
        request.state.request_id = rid
        token = request_id_ctx.set(rid)
        try:
            response = await call_next(request)
        finally:
            request_id_ctx.reset(token)
        response.headers["X-Request-ID"] = rid
        return response
```

Two mechanisms, both useful:

- **`request.state`** — available anywhere you have the `Request` object.
- **A `ContextVar`** — available in code that has no request, including a logging
  filter, so *every* log line in that request carries the id automatically.
  `ContextVar` is asyncio-safe: each task gets its own value.

Then the id shows up in three places: the response header, the error envelope
(Day 06), and every log line. A user reports "request `a1b2c3` failed" and you
have the whole story in one `grep`. Always **propagate** an inbound id rather than
generating a new one — that is how the trace survives across services.

## 8. Structured request logging

```python
logger.info("request", extra={
    "request_id": rid, "method": request.method, "path": request.url.path,
    "status": response.status_code, "duration_ms": round(elapsed * 1000, 2),
    "client": request.client.host if request.client else None,
})
```

One line per request, as **structured** fields rather than an f-string, so a log
platform can filter by status and sort by duration (Day 21 formats it as JSON).

Two things not to log:

- **Bodies and headers by default.** They contain passwords, tokens and personal
  data. If you need bodies for debugging, sample them, redact known fields, and
  turn it off in production.
- **Query strings blindly.** `?token=…` is common, and now the secret is in your
  log retention for a year.

Also log the client IP correctly behind a proxy: `request.client.host` is the
*proxy*. Use `X-Forwarded-For` — but only trust it when the request came through
your own proxy, or a client can forge it. Uvicorn's `--proxy-headers` with
`--forwarded-allow-ips` handles this properly.

## 9. CORS, explained once

A browser blocks cross-origin JavaScript from reading your response unless you say
otherwise. Server-to-server calls and `curl` are unaffected — which is why "it
works in curl but not the browser" is the classic CORS symptom.

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://app.shelfspace.com"],   # exact origins
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
    expose_headers=["X-Request-ID", "X-RateLimit-Remaining", "Location"],
    max_age=600,
)
```

| Setting | Meaning |
|---|---|
| `allow_origins` | exact origins (scheme + host + port). `*` disables credentials |
| `allow_credentials` | permits cookies / `Authorization`; **cannot** combine with `*` |
| `expose_headers` | headers JS may *read* — without it, your `X-Request-ID` is invisible |
| `max_age` | how long a browser caches the preflight |

**Preflight**: for anything beyond a simple request, the browser first sends
`OPTIONS` with `Access-Control-Request-Method`. The middleware answers it; your
routes never see it. If preflight fails, the real request is never sent — so
debug the `OPTIONS`, not the `POST`.

**`allow_origins=["*"]` with `allow_credentials=True` silently does not work** —
the spec forbids it, and browsers reject the response. Name your origins.

An origin is exact: `https://app.example.com` does not cover
`https://www.app.example.com`, and `http://localhost:3000` does not cover
`http://127.0.0.1:3000`. List every one you actually use, from settings, and
validate in production that `*` is not among them (Day 07).

## 10. Rate limiting

```python
class TokenBucket:
    """capacity tokens, refilled at `rate` per second; one token per request."""
    def allow(self, key: str, cost: int = 1) -> tuple[bool, int, float]: ...
```

A token bucket permits a **burst** up to the capacity and a steady rate after —
which matches how real clients behave better than a fixed window. Fixed windows
have a boundary problem: 100 requests at 11:59:59 and 100 more at 12:00:00 is 200
in one second, all "within limits".

What a good `429` looks like:

```http
HTTP/1.1 429 Too Many Requests
Retry-After: 12
X-RateLimit-Limit: 100
X-RateLimit-Remaining: 0
X-RateLimit-Reset: 1735689600

{"error": {"status": 429, "code": "rate_limited",
           "message": "Too many requests. Retry in 12 seconds."}}
```

`Retry-After` is what makes a well-behaved client back off instead of hammering
you harder. Send the `X-RateLimit-*` headers on **successful** responses too, so
clients can self-pace before they hit the wall.

Choices that matter:

| Decision | Guidance |
|---|---|
| Key by what? | API key or user id if authenticated; IP only as a fallback |
| IP limiting | remember NAT and mobile carriers put thousands behind one IP |
| Where to store counters | Redis — an in-process dict means each worker has its own limit |
| Different limits per endpoint | yes: reads cheap, writes and search expensive |
| Login endpoints | limit hard, by IP **and** by account (Day 15) |
| Health checks | exempt, or your monitoring rate-limits itself |

For production, `slowapi` or a gateway/CDN-level limiter is usually the right
call — the cheapest request to serve is the one your app never sees. Write your
own today to understand what the library does.

## 11. Middleware or dependency?

| Use middleware when | Use a dependency when |
|---|---|
| it applies to **every** request | it applies to some routes |
| it must run before routing | it needs path/body parameters |
| you need the raw request/response | you want the result injected |
| it must run even on 404 and 422 | it can run after validation |

Rate limiting and request IDs are middleware. Authentication is usually a
dependency (Day 15) — it needs to be per-route, must integrate with OpenAPI, and
benefits from `dependency_overrides` in tests. "Every request needs it" is a
weaker argument than "it should be visible in the signature and swappable in
tests".

## 12. The rest of the stack

```python
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["api.shelfspace.com"])
app.add_middleware(HTTPSRedirectMiddleware)          # production, if not at the proxy
```

- **GZip**: set a `minimum_size` — compressing a 40-byte response costs CPU and
  makes it *bigger*. Never compress already-compressed payloads (images, video),
  and be aware of BREACH if you compress responses containing secrets alongside
  attacker-controlled input.
- **TrustedHost**: rejects requests with a forged `Host` header, which otherwise
  poisons absolute URLs you generate (password-reset links, `Location`).
- **Body limits**: check `Content-Length` and reject `413` *before* reading, and
  also cap the streamed read — `Content-Length` can lie.
- **Security headers**: `X-Content-Type-Options: nosniff`,
  `X-Frame-Options: DENY`, and `Strict-Transport-Security` in production.

Every middleware runs on every request. Keep them cheap: no database calls, no
blocking I/O, no per-request object graph construction. A 5 ms middleware on a
20 ms endpoint is a 25% regression across your whole API.

## 13. Best practices introduced today

| Practice | Reason |
|---|---|
| Request ID middleware outermost | every other layer and log line needs it |
| Propagate an inbound `X-Request-ID` | the trace survives across services |
| Store it in both `request.state` and a `ContextVar` | reachable with or without the request |
| One structured log line per request | filterable, sortable, aggregatable |
| Never log bodies, tokens or raw query strings | logs are retained for a long time |
| Trust `X-Forwarded-For` only from your proxy | otherwise clients forge their IP |
| Name exact CORS origins from settings | `*` + credentials silently fails |
| `expose_headers` for anything JS must read | otherwise your headers are invisible |
| Debug CORS at the `OPTIONS` preflight | the real request never fires if it fails |
| CORS outermost enough to cover error responses | a 500 without CORS looks like a CORS bug |
| Token bucket over fixed window | no boundary burst |
| `Retry-After` on every `429` | it is what makes clients back off |
| Rate-limit counters in Redis, not in-process | per-worker limits are not limits |
| Key limits by API key/user, IP as fallback | NAT puts thousands behind one IP |
| Reject oversized bodies before reading them | validation happens too late to save memory |
| `TrustedHostMiddleware` in production | Host-header poisoning corrupts generated URLs |
| `minimum_size` on GZip | tiny compressed responses get bigger |
| Keep middleware cheap and non-blocking | it runs on every single request |
| Middleware for cross-cutting, dependencies for per-route | testability and OpenAPI integration |

## 14. Common mistakes

| Symptom | Cause | Fix |
|---|---|---|
| "CORS error" in the browser, curl fine | no CORS middleware | add it with exact origins |
| CORS works, credentials do not | `allow_origins=["*"]` with credentials | name the origins |
| Preflight fails, `POST` never sent | method/header not allowed | add them to `allow_*` |
| JS cannot read `X-Request-ID` | not in `expose_headers` | expose it |
| CORS headers missing on 500s | CORS added inside the error path | order it outermost |
| Middleware seems to run in reverse | it does — last added runs first | reorder deliberately |
| Rate limit resets per worker | in-process counter | Redis |
| Every user rate-limited together | keyed by a shared NAT IP | key by API key/user |
| Clients retry a 429 immediately | no `Retry-After` | send it |
| Monitoring rate-limits itself | health check not exempt | exempt it |
| Streaming responses buffered | `BaseHTTPMiddleware` | pure ASGI middleware |
| Whole API 20% slower | a database call in middleware | move it to a dependency |
| 500 with no error envelope | exception raised *in* middleware | handle it there |
| Passwords in the logs | logging request bodies | never by default |
| Real client IP always the proxy | no `--proxy-headers` | configure it, with allowed IPs |
| Password-reset links point to evil.com | Host header trusted | `TrustedHostMiddleware` |
| Worker OOMs on a large upload | body read before the size check | reject on `Content-Length`, cap the stream |

## 15. Exercises

1. Build the request-ID middleware with a `ContextVar` and a logging filter, then
   confirm one `grep` of an id shows the request log line *and* the error
   envelope.
2. Add timing with a slow-request `WARNING` above a configurable threshold, then
   hit `/debug/slow?seconds=2`.
3. Add CORS for `http://localhost:3000` and reproduce a preflight with `curl -X
   OPTIONS`. Then break it — remove `Content-Type` from `allow_headers` — and
   observe which request fails.
4. Try `allow_origins=["*"]` with `allow_credentials=True` and read what the
   browser says. Write down why the spec forbids it.
5. Implement the token bucket with `Retry-After` and `X-RateLimit-*` headers.
   Then run two workers (`uvicorn --workers 2`) and demonstrate that an
   in-process counter is not a rate limit.
6. Add a body-size limit that returns `413` from `Content-Length`, then send a
   chunked request with no `Content-Length` and fix what breaks.
7. Reorder the stack so GZip runs before request-ID and describe exactly what
   breaks.
8. Convert one `BaseHTTPMiddleware` to pure ASGI middleware and measure the
   difference under load.

## 16. What's next

**[Day 14 — Async and Concurrency →](../14_async_and_concurrency/)**
You have written `async def` for thirteen days without needing to know what it
means. Tomorrow you do: the event loop, why one blocking call freezes every
request, `def` versus `async def`, `gather`, and how to measure the difference.
