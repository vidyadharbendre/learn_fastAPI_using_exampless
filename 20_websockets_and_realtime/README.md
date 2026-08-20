# Day 20 — WebSockets and Realtime

> **Goal:** push updates instead of being polled — WebSockets for two-way
> traffic, SSE for one-way, a connection manager that survives more than one
> worker, and the judgement to know when polling was fine all along.
> **Time:** ~2.5 hours · **Port:** 8020 · **Builds on:** Day 19

> **Code status:** the README is the spec. Build `shelfspace/` yourself from
> sections 5 onwards before reaching for a reference implementation.

---

## 1. Why this matters

> **A thousand clients polling `/jobs/42` every second is a thousand requests a
> second to say "not yet".**

Realtime fixes that, and introduces problems HTTP had already solved for you.
A WebSocket is a **long-lived, stateful connection**: it holds memory for hours,
it does not go through your normal middleware, it is not authenticated by the
`Authorization` header a browser refuses to send, and it lives in the memory of
**one** worker process — so the update that must reach a user connected to worker
3 is broadcast by worker 1 and disappears.

Today covers all four, and — just as importantly — when *not* to use any of it.

## 2. What you will build

```
20_websockets_and_realtime/
├── run.py
└── shelfspace/
    ├── realtime/
    │   ├── manager.py      connections, rooms, broadcast, cleanup
    │   ├── auth.py         authenticating a socket (it is not a header)
    │   ├── pubsub.py       Redis fan-out across workers
    │   └── protocol.py     a typed message envelope, validated both ways
    ├── api/v1/
    │   ├── ws.py           /ws/jobs/{id}, /ws/catalogue
    │   └── sse.py          /events/jobs/{id}
    └── static/demo.html    a browser client to poke it with
```

## 3. Run it

```bash
source .venv/bin/activate
cd 20_websockets_and_realtime
python run.py
open http://127.0.0.1:8020/static/demo.html
```

## 4. Try it — learn by doing

```bash
API=http://127.0.0.1:8020
pip install websockets                      # for the CLI client below

# --- connect, subscribe, receive ---
python - <<'PY'
import asyncio, json, websockets
async def main():
    async with websockets.connect("ws://127.0.0.1:8020/ws/catalogue?token=dev-token") as ws:
        await ws.send(json.dumps({"type": "subscribe", "room": "books"}))
        for _ in range(5):
            print(json.loads(await ws.recv()))
asyncio.run(main())
PY
# in another terminal, create a book — the message arrives without polling:
curl -sX POST $API/api/v1/books -H 'Content-Type: application/json' \
  -d '{"isbn":"978-1-11-111111-2","title":"Live","price":"100.00","stock":1,"author_id":1}'

# --- SSE needs no library at all ---
curl -N $API/api/v1/events/jobs/1
# data: {"status":"running","progress":40}
# data: {"status":"succeeded"}

# --- a socket without a valid token is closed, not 401'd ---
python -c "
import asyncio, websockets
async def m():
    try:
        async with websockets.connect('ws://127.0.0.1:8020/ws/catalogue') as ws:
            print(await ws.recv())
    except Exception as e: print('closed:', e)
asyncio.run(m())"

# --- disconnects are cleaned up ---
curl -s $API/api/v1/ws/stats | python -m json.tool     # connections: N
# kill a client, then:
curl -s $API/api/v1/ws/stats | python -m json.tool     # N-1, not N

# --- MULTI-WORKER: the problem, then the fix ---
uvicorn shelfspace.main:app --workers 2 --port 8020 &
# connect two clients (they land on different workers), broadcast once:
curl -sX POST $API/api/v1/announce -H 'Content-Type: application/json' -d '{"text":"hello"}'
# without pub/sub: only one client hears it. With Redis: both do.

# --- back-pressure: a slow client must not stall the server ---
python - <<'PY'
import asyncio, websockets
async def main():
    ws = await websockets.connect("ws://127.0.0.1:8020/ws/firehose?token=dev-token")
    await asyncio.sleep(30)          # connect, then never read
asyncio.run(main())
PY
# the server should drop it, not buffer forever — watch the log

# --- idle connections are pinged and reaped ---
curl -s $API/api/v1/ws/stats | python -c "import json,sys; print(json.load(sys.stdin)['idle_closed'])"
```

## 5. Which one do you actually need?

| | Polling | SSE | WebSocket |
|---|---|---|---|
| Direction | client pulls | **server → client** | **both ways** |
| Protocol | HTTP | HTTP | upgraded, then not HTTP |
| Reconnect | trivial | **automatic, with `Last-Event-ID`** | you implement it |
| Proxies/CDNs | fine | fine | often need configuration |
| Middleware, auth, logging | all of it | all of it | **none of it** |
| Cost per client | a request per interval | one open connection | one open connection |
| Complexity | none | low | high |

Choose deliberately:

- **Polling** — status that changes rarely, or a job that takes minutes.
  `GET /jobs/42` every 5 seconds is *fine*, and it survives restarts, load
  balancers and network changes with no code.
- **SSE** — one-way updates: job progress, notifications, live dashboards. It is
  plain HTTP, browsers reconnect automatically, and it is dramatically simpler
  than WebSockets. **Most "realtime" requirements are actually SSE.**
- **WebSocket** — genuinely bidirectional and low-latency: chat, collaborative
  editing, multiplayer, live trading.

> Reach for a WebSocket when the client sends messages too. If it only listens,
> use SSE and save yourself most of this page.

## 6. The FastAPI shape of a socket

```python
@router.websocket("/ws/jobs/{job_id}")
async def job_updates(websocket: WebSocket, job_id: int):
    user = await authenticate_socket(websocket)          # BEFORE accept()
    if user is None or not can_view_job(user, job_id):
        await websocket.close(code=4401)                 # policy close code
        return

    await websocket.accept()
    await manager.connect(websocket, room=f"job:{job_id}")
    try:
        while True:
            raw = await websocket.receive_text()
            await handle_message(websocket, user, raw)
    except WebSocketDisconnect:
        pass                                             # normal, not an error
    finally:
        manager.disconnect(websocket)                    # ALWAYS
```

Four things to get right:

- **Authenticate before `accept()`.** Once accepted, a rejection is a close frame
  the client has to interpret; before it, you simply refuse the upgrade.
- **`WebSocketDisconnect` is normal.** Users close tabs and lose signal. Do not
  log it as an error, or your error rate becomes meaningless.
- **`finally: disconnect()`** — every path must remove the connection, or you
  leak memory and broadcast to dead sockets.
- **Close codes carry meaning.** `1000` normal, `1001` going away, `1008` policy
  violation, and `4000–4999` for your own (`4401` unauthenticated, `4403`
  forbidden, `4429` rate limited). A client can act on those; a bare disconnect
  is a mystery.

## 7. Authentication, because there is no `Authorization` header

The browser `WebSocket` API cannot set headers. Your options:

| Approach | Notes |
|---|---|
| **Query parameter token** (`?token=…`) | simple; **tokens leak into access logs** |
| **Cookie** | sent automatically; you must handle CSRF/origin checks |
| **First-message auth** | accept, then require an `auth` message within N seconds |
| **Ticket** | `POST /ws/ticket` returns a single-use, 30-second token | 

The **ticket** pattern is the good one: authenticate normally over HTTP, get a
short-lived single-use token, connect with it. Nothing long-lived is ever in a
URL, and the ticket is useless the moment it is used or expires.

Whatever you choose:

- **Check the `Origin` header.** Browsers do not apply CORS to WebSockets — a
  page on any site can open a socket to your server and, with cookie auth, it will
  be authenticated. This is CSRF for WebSockets, and the origin check is the
  defence.
- **Re-check authorization per message**, not only at connect. A connection can
  outlive the permission that opened it.
- **Rate limit messages.** Day 13's middleware does not run here; a client can
  send thousands of frames a second.

## 8. The connection manager

```python
class ConnectionManager:
    def __init__(self):
        self.rooms: dict[str, set[WebSocket]] = defaultdict(set)

    async def connect(self, ws: WebSocket, room: str) -> None:
        self.rooms[room].add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        for members in self.rooms.values():
            members.discard(ws)

    async def broadcast(self, room: str, message: dict) -> None:
        dead = []
        for ws in list(self.rooms.get(room, ())):
            try:
                await ws.send_json(message)
            except (WebSocketDisconnect, RuntimeError):
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)
```

Details that matter in production:

- **Iterate over a copy.** Sending can disconnect a client, mutating the set
  mid-iteration.
- **A failed send is not fatal.** Collect the dead and remove them afterwards;
  one broken client must not abort the broadcast.
- **Bound what you keep.** Rooms, per-user connection counts and message sizes
  all need caps, or one client with a reconnect loop consumes your memory.
- **Heartbeats.** Send a ping every ~30 seconds and drop connections that stop
  answering. Without it, half-open connections (a laptop lid closed on a train)
  linger for hours — and many proxies kill idle connections at 60 seconds anyway,
  so the ping keeps legitimate ones alive too.
- **Back-pressure.** A client that does not read makes `send` block or buffer
  without limit. Use a bounded per-connection queue and disconnect the client when
  it overflows. A slow consumer must not become your memory leak.

## 9. Multiple workers: the problem you will definitely hit

The manager above lives in **one process's memory**. With `--workers 4`, a
broadcast reaches a quarter of your users, and it works perfectly in development
where you run one worker.

The fix is an external pub/sub, with Redis the usual choice:

```python
# publish from anywhere: an endpoint, a background job (Day 18)
await redis.publish("room:books", json.dumps(message))

# each worker subscribes once, at startup, and fans out locally
async def pubsub_listener(app):
    async with redis.pubsub() as ps:
        await ps.psubscribe("room:*")
        async for msg in ps.listen():
            if msg["type"] == "pmessage":
                room = msg["channel"].decode().removeprefix("room:")
                await manager.broadcast(room, json.loads(msg["data"]))
```

Start that listener in `lifespan` (Day 01) and cancel it on shutdown.

Two consequences to plan for:

- **Sticky sessions are not required** with pub/sub, which is the point — any
  worker can serve any client.
- **Redis pub/sub does not persist.** A client that is disconnected during a
  broadcast misses it entirely. If messages matter, give each a sequence number
  and let clients request what they missed on reconnect (this is what SSE's
  `Last-Event-ID` does for you).

## 10. Server-Sent Events: the option to try first

```python
@router.get("/events/jobs/{job_id}")
async def job_events(job_id: int, request: Request, user: CurrentUser):
    async def stream():
        last = None
        while not await request.is_disconnected():
            job = await get_job(job_id)
            if job.status != last:
                last = job.status
                yield f"id: {job.updated_at.timestamp()}\n"
                yield f"event: status\n"
                yield f"data: {json.dumps({'status': job.status})}\n\n"
            if job.status in TERMINAL:
                return
            await asyncio.sleep(1)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",         # tell nginx not to buffer
        "Connection": "keep-alive",
    })
```

```javascript
const es = new EventSource("/api/v1/events/jobs/42");
es.addEventListener("status", e => console.log(JSON.parse(e.data)));
// reconnects automatically, and sends Last-Event-ID
```

What SSE gives you for almost no work: automatic reconnection with
`Last-Event-ID` (so you can resume), plain HTTP (so your auth, middleware and
logging still apply), and no protocol upgrade for proxies to mishandle.

Its limits: one-way only, text only, and browsers cap connections per origin over
HTTP/1.1 (6, shared with everything else on the page) — use HTTP/2 in production,
where that limit effectively disappears.

Note `X-Accel-Buffering: no`: nginx buffers proxied responses by default, which
holds your events until the buffer fills. Cloud load balancers have equivalents,
and "SSE works locally, not in production" is almost always this.

## 11. Messages, and testing

Validate messages **in both directions** with Pydantic (Days 04–05) — an
unvalidated socket message is an unvalidated request body, with the same
consequences:

```python
class ClientMessage(BaseModel):
    type: Literal["subscribe", "unsubscribe", "ping"]
    room: str | None = Field(default=None, max_length=64)

msg = ClientMessage.model_validate_json(raw)      # ValidationError → close 1008
```

Include a `type` on every message from day one, so adding a new kind later does
not break clients that switch on it.

Testing is straightforward — `TestClient` speaks WebSocket:

```python
def test_broadcast_reaches_subscriber(client):
    with client.websocket_connect("/ws/catalogue?token=t") as ws:
        ws.send_json({"type": "subscribe", "room": "books"})
        client.post("/api/v1/books", json=payload)
        assert ws.receive_json()["type"] == "book.created"

def test_socket_without_token_is_rejected(client):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/catalogue") as ws:
            ws.receive_text()
```

Test the disconnect paths too: they are where leaks live, and a `ws/stats`
endpoint makes the leak visible in a test.

## 12. Best practices introduced today

| Practice | Reason |
|---|---|
| Prefer polling or SSE unless you need two-way | most "realtime" is one-way |
| Authenticate **before** `accept()` | a refused upgrade beats a close frame |
| Use a short-lived ticket, not a long-lived token in the URL | URLs land in access logs |
| Check the `Origin` header | CORS does not protect WebSockets |
| Re-authorize per message | connections outlive permissions |
| Rate limit messages in the handler | middleware does not run for sockets |
| `finally: disconnect()` on every path | otherwise you leak and broadcast to the dead |
| Treat `WebSocketDisconnect` as normal | it is a user closing a tab |
| Meaningful close codes (4401/4403/4429) | the client can react |
| Iterate over a copy when broadcasting | sending can mutate the set |
| Heartbeat ping/pong with a reap timeout | half-open connections linger for hours |
| Bounded per-connection queues | a slow consumer is a memory leak |
| Cap rooms, connections per user, message size | one looping client must not sink you |
| Redis pub/sub for multi-worker fan-out | in-memory managers are per process |
| Sequence numbers for messages that matter | pub/sub does not replay |
| Validate messages with Pydantic both ways | a socket frame is an untrusted input |
| A `type` field on every message | forward-compatible clients |
| `X-Accel-Buffering: no` for SSE | proxies buffer and hide your events |
| A stats endpoint for connection counts | leaks otherwise stay invisible |

## 13. Common mistakes

| Symptom | Cause | Fix |
|---|---|---|
| Works with 1 worker, not 4 | in-memory connection registry | Redis pub/sub |
| Only some users get a broadcast | same | same |
| Memory grows all day | connections never removed | `finally: disconnect()` |
| Error rate full of disconnects | logging normal closes as errors | catch `WebSocketDisconnect` |
| Connections die after 60 seconds | proxy idle timeout | heartbeat pings |
| Ghost connections for hours | no ping/reap | heartbeat + timeout |
| Server memory spikes from one client | no back-pressure | bounded queue, drop slow clients |
| Anyone can connect | no auth before `accept()` | authenticate first |
| Tokens in access logs | long-lived token in the query string | ticket pattern |
| Socket opened from another site | no `Origin` check | check it |
| A user keeps access after being banned | authorized only at connect | re-check per message |
| Client floods the server | no message rate limit | limit in the handler |
| Crash on malformed input | messages not validated | Pydantic + close `1008` |
| Broadcast aborts halfway | one failed send raised | collect and skip dead sockets |
| `RuntimeError: … after close` | sending to a closed socket | remove on failure |
| SSE works locally, not in prod | proxy buffering | `X-Accel-Buffering: no`, HTTP/2 |
| Browser stops opening SSE connections | 6-per-origin HTTP/1.1 limit | HTTP/2 |
| Missed updates after reconnecting | pub/sub has no replay | sequence numbers / `Last-Event-ID` |

## 14. Exercises

1. Implement `/events/jobs/{id}` with SSE and drive it from `curl -N` and an
   `EventSource` in the browser. Then decide honestly whether you need
   WebSockets at all.
2. Implement `/ws/catalogue` with rooms, and broadcast a `book.created` event
   when the Day 02 `POST /books` succeeds.
3. Add the ticket flow: `POST /ws/ticket` → single-use 30-second token → connect.
   Confirm reuse fails.
4. Add an `Origin` check and demonstrate a socket from another origin being
   refused.
5. Run `--workers 2`, connect two clients, and reproduce the split-brain
   broadcast. Then fix it with Redis pub/sub.
6. Add heartbeats and a reaper; simulate a half-open connection (suspend the
   client process) and confirm it is dropped.
7. Add a bounded send queue and prove a client that never reads is disconnected
   instead of consuming memory.
8. Write the three tests in section 11, plus one asserting the connection count
   returns to zero after a disconnect.

## 15. What's next

**[Day 21 — Observability, Docker and Deployment →](../21_observability_docker_and_deployment/)**
The last day. Structured logs and metrics you can actually query, a Docker image
that is small and safe, Gunicorn with Uvicorn workers, health and readiness
probes, CI, and the deployment checklist for everything the previous twenty days
built.
