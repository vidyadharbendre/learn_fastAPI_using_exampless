# Day 12 — REST API Design and Pagination

> **Goal:** design an API other people can use without asking you questions —
> one pagination envelope, allow-listed filtering and sorting, versioning you can
> live with, ETags, and idempotent writes.
> **Time:** ~2.5 hours · **Port:** 8012 · **Builds on:** Day 11

> **Code status:** the README is the spec. Build `shelfspace/` yourself from
> sections 5 onwards before reaching for a reference implementation.

---

## 1. Why this matters

> **An API is a contract with people you will never meet.**

They cannot ask what a field means, they will not read your source, and they
**will** retry after a timeout. Predictability beats cleverness every time.

You have the pieces already: routing (Day 02), validation (Day 04), output
contracts (Day 05), one error envelope (Day 06), fast queries (Day 11). Today
they become a *design* — a set of conventions applied identically to every
resource, so learning one endpoint teaches you all of them.

## 2. What you will build

```
12_rest_api_design_and_pagination/
├── run.py
└── shelfspace/
    ├── api/
    │   ├── pagination.py    Page[T], links, cursor encoding
    │   ├── filtering.py     the allow-lists
    │   ├── conditional.py   ETag / If-None-Match / If-Match
    │   ├── idempotency.py   Idempotency-Key handling
    │   └── v1/{books,authors}.py
    └── …
```

## 3. Run it

```bash
source .venv/bin/activate
cd 12_rest_api_design_and_pagination
alembic upgrade head && python -m shelfspace.seed --books 500
python run.py
```

```bash
curl -s http://127.0.0.1:8012/api/v1/ | python -m json.tool
```

A root document listing the endpoints costs one function and makes the API
explorable with `curl` alone.

## 4. Try it — learn by doing

```bash
API=http://127.0.0.1:8012/api/v1
JSON='Content-Type: application/json'

# --- one envelope: data, meta, links ---
curl -s "$API/books?per_page=2"                  | python -m json.tool
curl -s "$API/books?per_page=2&page=2"           | python -m json.tool

# --- links preserve the filters. Follow next twice and check q survives. ---
curl -s "$API/books?q=python&per_page=2" | python -c "
import json,sys; print(json.load(sys.stdin)['links'])"

# --- filtering and sorting, from an allow-list ---
curl -s "$API/books?sort=-price&per_page=3"      | python -m json.tool
curl -s "$API/books?min_price=500&max_price=5000" | python -m json.tool
curl -s "$API/books?sort=colour"                 | python -m json.tool  # 422 + allowed
curl -s "$API/books?per_page=999999"             | python -m json.tool  # capped

# --- cursor pagination for the large collection ---
curl -s "$API/books?limit=3"                     | python -m json.tool
curl -s "$API/books?limit=3&cursor=<paste next_cursor>" | python -m json.tool

# --- conditional GET: the second request transfers no body ---
ETAG=$(curl -sI $API/books/1 | awk '/[Ee]tag/{print $2}' | tr -d '\r')
curl -si $API/books/1 -H "If-None-Match: $ETAG" | head -3      # 304, empty body

# --- optimistic locking: two clients, one wins ---
curl -sX PUT $API/books/1 -H "$JSON" -H "If-Match: $ETAG" \
     -d '{"title":"Odyssey","price":"550.00","stock":5,"isbn":"978-0-14-303943-3","author_id":1}' \
  | python -m json.tool
curl -sX PUT $API/books/1 -H "$JSON" -H "If-Match: $ETAG" \
     -d '{"title":"Odyssey II","price":"560.00","stock":5,"isbn":"978-0-14-303943-3","author_id":1}' \
  | python -m json.tool                                        # 412 — stale ETag

# --- idempotent POST: the same key returns the SAME resource ---
KEY=$(uuidgen)
for i in 1 2; do
  curl -s -X POST $API/books -H "$JSON" -H "Idempotency-Key: $KEY" \
    -d '{"isbn":"978-1-11-111111-1","title":"Once Only","price":"100.00","stock":1,"author_id":1}' \
  | python -c "import json,sys; print(json.load(sys.stdin)['id'])"
done                                                            # the same id twice

# --- versioning and deprecation are visible in headers ---
curl -sI $API/books | grep -iE 'deprecation|sunset|link'
```

**Read the error bodies too.** Every failure still uses Day 06's envelope — that
consistency is the whole point of the design.

## 5. Resource design

URLs name **things**; the method says what you are doing to them. A verb in your
URL means you are writing RPC with extra steps.

| ✅ | ❌ |
|---|---|
| `GET /books` | `GET /getAllBooks` |
| `POST /books` | `POST /createBook` |
| `GET /books/42` | `GET /getBook?id=42` |
| `PUT /books/42` | `POST /updateBook` |
| `DELETE /books/42` | `POST /deleteBook` |

Plural collections · ids in the path · filters in the query string.

Both of these are fine, and a good API offers both:

```
GET /authors/5/books        # nested: expresses ownership, natural 404
GET /books?author_id=5      # filtered: composes with other filters
```

Nest **one level, at most two**. `/authors/5/books/12/reviews/3` is unreadable and
the ids after the first are usually globally unique anyway — `/reviews/3` says the
same thing.

**Actions that are not CRUD.** Some operations genuinely are verbs: `borrow`,
`cancel`, `publish`. Two honest options:

```
POST /books/42/borrow          # a controller sub-resource — pragmatic, common
POST /loans  {"book_id": 42}   # model the action as a resource — purer
```

Prefer the second when the action has its own lifecycle (a loan can be listed,
extended, returned). Use the first for a genuinely momentary state change, and do
not pretend `PATCH {"status": "borrowed"}` is more RESTful — it hides the rules.

## 6. One pagination envelope, everywhere

```json
{
  "data": [ … ],
  "meta":  {"page": 1, "per_page": 20, "total": 137, "pages": 7},
  "links": {"self": "…?page=1&q=python",
            "next": "…?page=2&q=python",
            "prev": null, "first": "…", "last": "…"}
}
```

| Rule | Reason |
|---|---|
| **Always** paginate collections | works with 50 rows, takes the site down at 500,000 |
| **Cap** `per_page` (100 here) | `?per_page=1000000` is a DoS you invited |
| Return `links.next` | clients stop building URLs, so you can change the scheme |
| **Preserve filters in links** | otherwise page 2 silently drops the search |
| Keep the envelope identical across resources | one client-side pager for the whole API |

In FastAPI, make it generic once:

```python
T = TypeVar("T")

class Page(BaseModel, Generic[T]):
    data: list[T]
    meta: PageMeta
    links: PageLinks

@router.get("/books", response_model=Page[BookPublic])
```

`Page[BookPublic]` and `Page[AuthorPublic]` both appear correctly in
`/openapi.json`, so generated clients get real types.

**Cursor pagination** for large or actively-written collections (Day 11):

```json
{"data": [...], "meta": {"limit": 20, "has_more": true},
 "links": {"next": "/books?limit=20&cursor=eyJpZCI6NDIwfQ"}}
```

Encode the cursor (base64 of `{"id": 420}`) so it is opaque — clients must not
construct it, or you can never change what it contains. Sign it if a forged
cursor could leak data. Note that `total` usually disappears: `COUNT(*)` on a huge
table costs as much as the page itself.

## 7. Filtering and sorting from an allow-list

```python
SORTABLE = {"title": Book.title, "price": Book.price, "created_at": Book.created_at}

field = sort.lstrip("-")
if field not in SORTABLE:
    raise APIError(422, "invalid_sort", f"Cannot sort by {field!r}.",
                   details={"allowed": sorted(SORTABLE)})
column = SORTABLE[field]
stmt = stmt.order_by(column.desc() if sort.startswith("-") else column.asc())
```

Never interpolate a query parameter into SQL, and never `getattr(Model, field)` —
both let a client reach columns you never meant to expose (`password_hash`,
anyone?). Mapping to real column objects also turns an unknown field into a clean
`422` **with the allowed values**, which is *kind*.

Give filters a consistent grammar across the whole API and document it:

```
?min_price=  ?max_price=          range
?author_id=  ?tag=                exact (repeat the key for OR)
?q=                               free text
?created_after=  ?created_before=  time range
?sort=-price,title                multi-key, `-` for descending
```

Whatever you choose, apply it identically everywhere. An API where one endpoint
uses `?price_min` and another `?minPrice` costs every client an extra lookup,
forever.

## 8. Conditional requests: ETags

```python
def etag_for(obj) -> str:
    return '"' + hashlib.sha256(
        f"{obj.id}:{obj.updated_at.isoformat()}".encode()).hexdigest()[:32] + '"'

@router.get("/books/{id}")
async def get_book(id: int, request: Request, response: Response):
    book = ...
    etag = etag_for(book)
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304)          # no body, no bandwidth
    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = "private, max-age=60"
    return book
```

Two payoffs from the same header:

**Caching.** A `304` transfers headers only. On a mobile client polling a
catalogue, that is most of your bandwidth bill.

**Optimistic locking.** `If-Match` turns lost updates into a visible conflict:

```
client A GET  /books/1     → ETag "abc"
client B GET  /books/1     → ETag "abc"
client A PUT  If-Match: "abc"  → 200, now "def"
client B PUT  If-Match: "abc"  → 412 Precondition Failed   ← B's edit is not lost
```

Without it, B's write silently overwrites A's. That is *the* concurrency bug in
CRUD applications, and it costs one header to fix.

Use a **strong** validator derived from something that always changes on write
(`updated_at`, or a `version` integer). Hashing the serialised body works too but
is expensive and changes when your serialisation changes.

## 9. Idempotent writes

`POST` is not idempotent, so a client that retries after a timeout creates a
second book. The standard fix is a client-supplied key:

```python
key = request.headers.get("Idempotency-Key")
if key:
    if cached := idempotency.get(key):          # same key, same endpoint, same user
        return cached.response                  # replay, do not re-execute
    ...
    idempotency.store(key, response, ttl=timedelta(hours=24))
```

Rules that make it correct rather than decorative:

- Scope the key to **user + endpoint**, not globally — otherwise one client's key
  can replay another's response.
- Store the **response**, not just "seen", so the retry gets the created object
  and the same status code.
- Treat "same key, different body" as a `422` — it means a client bug.
- Give keys a TTL (24 hours is typical) and store them in Redis or the database,
  not a process-local dict, or they vanish on restart and differ per worker.

`PUT` and `DELETE` are naturally idempotent; this is a `POST` concern.

## 10. Versioning and deprecation

```python
app.include_router(api_router_v1, prefix="/api/v1")
app.include_router(api_router_v2, prefix="/api/v2")
```

Version from the first commit (Day 07). Once a third party depends on a URL you
cannot make a breaking change to it.

| Change | Breaking? |
|---|---|
| Adding an optional field to a response | no |
| Adding an optional query parameter | no |
| Removing or renaming a field | **yes** |
| Tightening validation | **yes** |
| Changing a status code | **yes** |
| Changing the meaning of a field | **yes**, and the worst kind |

URL versioning (`/api/v1`) is not the most elegant option — header versioning is
purer — but it is visible in logs, curl-able, and cache-friendly. Elegance loses
to debuggability here.

When you retire something, say so in headers rather than in an email nobody read:

```
Deprecation: Wed, 01 Oct 2026 00:00:00 GMT
Sunset: Tue, 01 Mar 2027 00:00:00 GMT
Link: </api/v2/books>; rel="successor-version"
```

And log usage per version, so you know who is still on `v1` before you switch it
off.

## 11. Small things that make an API pleasant

```python
response.headers.setdefault("X-Content-Type-Options", "nosniff")
```

| Practice | Payoff |
|---|---|
| A root document (`GET /api/v1/`) listing endpoints | discoverable with `curl` |
| Consistent field naming across resources | one convention to learn |
| `created_at` / `updated_at` on every resource | clients build caches and sync |
| Ids as strings if they might exceed 2^53 | JavaScript silently rounds big integers |
| `nosniff` on every response | stops a browser rendering JSON as HTML |
| `406` only when the client explicitly asks for something you cannot produce | being stricter breaks `curl` defaults |
| Never expose sequential ids where enumeration matters | use UUIDs for anything guessable |

CORS is the other thing clients trip over, and it is middleware — Day 13.

## 12. Best practices introduced today

| Practice | Reason |
|---|---|
| Nouns in URLs, verbs as methods | that *is* REST |
| Nest at most one level | deep nesting is unreadable and unnecessary |
| Model real actions as resources when they have a lifecycle | `/loans` beats `PATCH status` |
| One pagination envelope for every collection | one client-side pager for the whole API |
| Always paginate; cap `per_page` | uncapped collections are a DoS |
| Links preserve every active filter | page 2 must keep the search |
| Cursor pagination for large or live tables | offset degrades and is unstable |
| Opaque, encoded cursors | clients must not construct them |
| Allow-list sorting and filtering | injection-proof, and 422 not 500 |
| One filter grammar across resources | learn once, use everywhere |
| ETags for `304` and `If-Match` | bandwidth, and no lost updates |
| Idempotency keys scoped per user + endpoint | retries stop creating duplicates |
| Version from commit one; URL versioning | debuggable and cache-friendly |
| Announce removal with `Deprecation`/`Sunset` | email is not a deprecation policy |
| Track usage per version | switch off with evidence, not hope |
| A discoverable root document | `curl` becomes the documentation |
| `nosniff` on every response | contains XSS from echoed input |

## 13. Common mistakes

| Symptom | Cause | Fix |
|---|---|---|
| Client retry created two books | `POST` without an idempotency key | add one |
| Timeouts on a big collection | no pagination | paginate and cap |
| `?per_page=1000000` melts the server | no cap | clamp it |
| Page 2 loses the search | filters dropped from links | thread the query string through |
| Same item on two pages | offset paging on a live table | cursors |
| Client builds its own cursors | cursor was transparent | encode it |
| `500` on a bad `?sort=` | interpolated user input | allow-list |
| Client sorted by `password_hash` | `getattr(Model, field)` | allow-list |
| Two clients' edits, one survives | no `If-Match` | optimistic locking |
| Mobile app burns bandwidth polling | no ETags | conditional GET |
| Breaking change shipped to `/v1` | no versioning policy | version, and define "breaking" |
| Nobody noticed a deprecation | announced by email only | `Deprecation`/`Sunset` headers |
| JS client shows wrong ids | ids above 2^53 as JSON numbers | send ids as strings |
| Resource ids enumerable | sequential integers | UUIDs where it matters |
| `?minPrice` here, `?price_min` there | no filter grammar | one convention |
| Browser blocks every request | no CORS headers | Day 13 |

## 14. Exercises

1. Implement `Page[T]` with `data`/`meta`/`links`, use it on `/books` and
   `/authors`, and confirm `/openapi.json` shows both parameterised types.
2. Make `links.next` preserve `q`, `min_price` and `sort`. Then write a test that
   follows `next` until exhaustion and asserts no item appears twice.
3. Add cursor pagination with an opaque base64 cursor, and return `422` for a
   malformed one.
4. Add ETags to `GET /books/{id}` and prove a `304` returns an empty body.
5. Add `If-Match` to `PUT` and reproduce the lost-update race in section 8 — first
   without the header, then with it.
6. Implement idempotency keys backed by the database, including "same key,
   different body → 422". Then reason about what happens with two workers.
7. Ship `/api/v2/books` that renames one field, add `Deprecation` and `Sunset`
   headers to `v1`, and log usage per version.
8. Write the filter grammar for your API as a table, then check every existing
   endpoint against it and fix the outliers.

## 15. What's next

**[Day 13 — Middleware, CORS and Rate Limiting →](../13_middleware_cors_and_rate_limiting/)**
Everything so far happens *inside* an endpoint. Tomorrow you work on the layer
around all of them: request IDs, timing, compression, the CORS headers a browser
demands, and the rate limiter that keeps one client from consuming the whole
service.
