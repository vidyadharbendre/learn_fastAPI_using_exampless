# Day 02 — HTTP Methods and Routing

> **Goal:** turn a read-only catalogue into one that accepts writes — the five
> verbs, the status code each one owes the client, and the routing rules that
> decide *which* function actually runs.
> **Time:** ~2 hours · **Port:** 8002 · **Builds on:** Day 01

> **Code status:** the README is the spec. Build `shelfspace/` yourself from
> sections 5 onwards before reaching for a reference implementation — that is
> where the learning is.

---

## 1. Why this matters

> **HTTP already decided what your endpoints mean. You can cooperate or you can
> spend a year explaining yourself.**

A client that times out on a `POST` does not know whether the book was created.
A client that times out on a `PUT` does — it just retries. That difference is not
politeness; it is a property of the method you chose, and browsers, proxies, CDNs
and every HTTP library in existence rely on it whether you designed for it or not.

Today's code is short and mostly mechanical. The *decisions* are the lesson.

## 2. What you will build

Full CRUD over the bookstore catalogue:

```
02_http_methods_and_routing/
├── run.py
└── shelfspace/
    ├── __init__.py
    ├── config.py       (Day 01, unchanged)
    ├── data.py         an in-memory store with next_id() and helpers
    ├── schemas.py      Book, BookCreate, BookReplace, BookPatch
    └── main.py         GET · POST · PUT · PATCH · DELETE + routing demos
```

| Method | Path | Status | Meaning |
|---|---|---|---|
| `GET` | `/books` | 200 | list the catalogue |
| `GET` | `/books/{id}` | 200 / 404 | one book |
| `POST` | `/books` | **201** + `Location` | create |
| `PUT` | `/books/{id}` | 200 | replace the whole book |
| `PATCH` | `/books/{id}` | 200 | change some fields |
| `DELETE` | `/books/{id}` | **204** (empty) | remove |
| `GET` | `/books/bestsellers` | 200 | a static path that must win over `{id}` |

## 3. Run it

```bash
source .venv/bin/activate
cd 02_http_methods_and_routing

python run.py            # or: uvicorn shelfspace.main:app --reload --port 8002
```

<http://127.0.0.1:8002/docs> now groups the operations by verb and colour —
Swagger UI is reading the same method semantics you are learning today.

## 4. Try it — learn by doing

```bash
API=http://127.0.0.1:8002

# --- read ---
curl -s  $API/books        | python -m json.tool
curl -s  $API/books/1      | python -m json.tool
curl -si $API/books/9999   | head -4                  # 404 with a JSON body

# --- create: watch the 201 AND the Location header ---
curl -isX POST $API/books -H "Content-Type: application/json" \
  -d '{"isbn":"978-1-4919-4600-8","title":"Fluent Python",
       "author":"Luciano Ramalho","price":"4199.00","stock":7}' | head -12

# --- run that exact POST again. Two books now exist. That is POST. ---
curl -s $API/books | python -m json.tool | grep -c '"id"'

# --- replace vs modify: the pair everyone gets wrong ---
curl -s $API/books/1 | python -m json.tool
curl -sX PATCH $API/books/1 -H "Content-Type: application/json" \
  -d '{"stock":99}' | python -m json.tool            # only stock changes
curl -sX PUT   $API/books/1 -H "Content-Type: application/json" \
  -d '{"isbn":"978-0-14-303943-3","title":"The Odyssey","author":"Homer",
       "price":"499.00","stock":5}' | python -m json.tool

# --- PUT twice. Identical result. That is idempotence. ---

# --- delete: 204 means the body must be EMPTY ---
curl -isX DELETE $API/books/2 | head -3
curl -isX DELETE $API/books/2 | head -3               # now 404 — gone is gone

# --- routing: static must beat dynamic ---
curl -s $API/books/bestsellers | python -m json.tool  # NOT a 422 about "bestsellers"

# --- what you get without writing a line ---
curl -sX POST   $API/books/1     | head -1            # 405 Method Not Allowed
curl -isX OPTIONS $API/books/1   | grep -i '^allow'   # the verbs this path takes
curl -sI $API/books              | head -3            # HEAD: headers, no body
curl -sX POST $API/books -H "Content-Type: application/json" \
  -d '{"title":"No price"}'      | python -m json.tool  # 422, field named
```

**Read the 422 body.** FastAPI tells you the exact field, the exact rule, and
where in the payload it was. You wrote none of that.

## 5. The five verbs, and the two properties that matter

| Method | Safe | Idempotent | Body | Meaning |
|---|---|---|---|---|
| `GET` | ✅ | ✅ | no | read; **never** changes state |
| `POST` | ❌ | ❌ | yes | create; twice creates two |
| `PUT` | ❌ | ✅ | yes | replace the whole resource |
| `PATCH` | ❌ | ❌* | yes | change some fields |
| `DELETE` | ❌ | ✅ | no | remove |

- **Safe** = changes nothing. Browsers prefetch links, crawlers follow them,
  proxies cache them. `GET /books/1/delete` *will* eventually be triggered by
  something that was only looking around.
- **Idempotent** = doing it twice leaves the same state as doing it once. This is
  the property that lets a client retry after a timeout — and clients retry
  whether or not you designed for it.

\* `PATCH` *can* be idempotent (`{"stock": 5}` is) and often is not
(`{"stock": "+1"}` is not). The spec does not promise it, so clients must not
assume it.

**`DELETE` is idempotent, not "always 200".** Deleting the same book twice leaves
the same state both times — the second call returning `404` is fine and honest.

## 6. `PUT` vs `PATCH` — the one that causes data loss

```bash
PUT   /books/1   {"title": "The Odyssey"}    # ❌ author, price, stock RESET
PATCH /books/1   {"title": "The Odyssey"}    # ✅ only the title changes
```

`PUT` is a **replacement**. Fields you omit are not "left alone" — they are gone,
because you just declared what the whole resource is. Implementing `PUT` as a
partial update is a bug that looks like kindness right up until a client relies
on it.

In FastAPI the distinction is enforced by the *schema*, not by discipline:

```python
class BookReplace(BaseModel):        # PUT — everything required
    isbn: str
    title: str
    author: str
    price: str
    stock: int

class BookPatch(BaseModel):          # PATCH — everything optional
    isbn: str | None = None
    title: str | None = None
    author: str | None = None
    price: str | None = None
    stock: int | None = None
```

And the update itself must distinguish *absent* from *explicitly null*:

```python
changes = payload.model_dump(exclude_unset=True)   # ✅ only keys the client SENT
changes = payload.model_dump()                     # ❌ every key, Nones included
```

`exclude_unset=True` is the whole trick. Without it, a `PATCH` of `{"stock": 5}`
also writes `title=None` over a perfectly good title.

## 7. Status codes you must get right

```python
@app.post("/books", status_code=201)
@app.delete("/books/{book_id}", status_code=204)
```

FastAPI returns `200` unless you say otherwise — so `201` and `204` are opt-in,
and forgetting them is the most common day-two mistake.

| Code | When | The detail people miss |
|---|---|---|
| `200 OK` | reads, `PUT`, `PATCH` | — |
| `201 Created` | successful `POST` | **must** include a `Location` header |
| `204 No Content` | successful `DELETE` | the body must be genuinely **empty** |
| `404 Not Found` | no such resource | the *path* is wrong |
| `405 Method Not Allowed` | wrong verb on a real path | free from the router |
| `422 Unprocessable Content` | body parsed, values invalid | free from Pydantic |

**`204` really means no body.** Returning `{"deleted": true}` with a `204` is a
protocol violation, and some HTTP clients raise on it. If you want to say
something, return `200` with a body. In FastAPI, use
`Response(status_code=204)` or annotate `response_class=Response`.

**A correct `POST` does three things:**

```http
HTTP/1.1 201 Created
Location: http://127.0.0.1:8002/books/4

{"id": 4, "title": "Fluent Python", ...}
```

201, not 200 · a `Location` header so the client never has to construct the URL
· the created object, including the id the server just assigned.

Build the header from the router, never by hand:

```python
response.headers["Location"] = str(request.url_for("get_book", book_id=book.id))
```

`url_for` uses the **function name** as the route name. Change the path later and
every `Location` header follows automatically; a hand-built f-string does not.

## 8. Routing: order decides the winner

```python
@app.get("/books/{book_id}")        # ← declared first
@app.get("/books/bestsellers")      # ← never runs
```

Starlette matches routes **in declaration order** and takes the first hit.
`/books/bestsellers` reaches the dynamic route first, `"bestsellers"` fails to
parse as an `int`, and the client gets a baffling `422` about a path they typed
correctly.

> **Rule: declare static paths before dynamic ones.** `/books/bestsellers` above
> `/books/{book_id}`. This is not a FastAPI quirk — every router with path
> parameters works this way.

Two more routing rules worth knowing on day two:

**Trailing slashes are different paths.** `/books` and `/books/` do not match the
same route; by default Starlette answers the mismatch with a `307 Temporary
Redirect`. A redirect on a `POST` is a trap — some clients drop the body, others
downgrade the method. Pick one form, use it everywhere, and consider
`redirect_slashes=False` so a mistake is a loud 404 instead of a silent redirect.

**404 vs 405 is a real distinction.** `404` = no such path. `405` = the path
exists, that verb does not. The router gives you the second one free, and it is
far more useful to a client than a blanket 404.

## 9. What the router does for you, unasked

| Request | Response | Why |
|---|---|---|
| `HEAD /books` | headers only, no body | derived from your `GET` |
| `OPTIONS /books/1` | `Allow: GET, PUT, PATCH, DELETE` | built from the routing table |
| `POST /books/1` | `405` | path known, verb not registered |
| malformed JSON | `400` | the parser refused |
| valid JSON, bad values | `422` + field errors | Pydantic |

`HEAD` matters more than it looks: monitoring tools and link checkers use it to
test a URL without transferring the payload. You get it for free — but only if
your `GET` is genuinely **safe**, i.e. it does not change anything.

## 10. Naming, tags, and the docs you get for free

```python
@app.get(
    "/books/{book_id}",
    response_model=Book,
    status_code=200,
    tags=["catalogue"],
    summary="Fetch one book",
    responses={404: {"description": "No book with that id"}},
)
async def get_book(book_id: int) -> Book:
    """This docstring becomes the endpoint's long description in /docs."""
```

`tags` group endpoints in Swagger UI — with more than a dozen routes, an
untagged API becomes an undifferentiated wall. `responses={404: ...}` documents
the failure paths, which is the half of an API that clients actually struggle
with. Day 06 makes those failures consistent.

## 11. Where the write logic should *not* live

Today's handlers touch `data.py` directly. That is fine for one file and stops
scaling almost immediately — by Day 10 the same logic needs to run inside a
transaction, and by Day 17 it needs to run in a test without HTTP.

Keep two habits now so those days are cheap:

- The handler **translates HTTP** (parse, call, choose a status code). It should
  not contain business rules.
- Data access lives in **one module**. Day 09 swaps `data.py` for SQLAlchemy and
  nothing above it changes — which only works if nothing above it reached in.

## 12. Best practices introduced today

| Practice | Reason |
|---|---|
| Nouns in URLs, verbs as HTTP methods | `/books` + `POST`, never `/createBook` |
| Plural collection names | `/books/1` reads correctly; `/book/1` does not |
| Honour safe and idempotent semantics | caches, proxies and retries depend on them |
| `201` + `Location` + the created body | the client never guesses the new URL |
| `204` with a genuinely empty body | some clients raise on a body |
| Build URLs with `url_for`, not f-strings | paths change; route names do not |
| Separate `BookReplace` from `BookPatch` | the schema enforces PUT vs PATCH |
| `model_dump(exclude_unset=True)` for PATCH | absent ≠ explicitly null |
| Static routes before dynamic routes | first match wins |
| Pick one trailing-slash form | a 307 on a POST can drop the body |
| Return `404` for a missing resource, `405` for a wrong verb | they mean different things |
| `tags` and `summary` on every route | docs stay usable past a dozen endpoints |
| Document failures in `responses={}` | clients struggle with the error paths |
| Handlers translate HTTP; logic lives below | Day 10 and Day 17 depend on it |

## 13. Common mistakes

| Symptom | Cause | Fix |
|---|---|---|
| `POST` returns 200 | forgot `status_code=201` | set it explicitly |
| Client can't find the new resource | no `Location` header | add it via `url_for` |
| Retry after a timeout creates duplicates | retried a `POST` | use `PUT`, or an idempotency key (Day 12) |
| `PUT` wiped `author` and `price` | client sent a partial body | that is correct — use `PATCH` |
| `PATCH` wiped unsent fields | `model_dump()` without `exclude_unset` | add `exclude_unset=True` |
| `PATCH {"stock": null}` ignored | can't tell absent from null | check `payload.model_fields_set` |
| `422` on `/books/bestsellers` | dynamic route declared first | move the static route above it |
| `307` on a `POST`, body lost | trailing-slash mismatch | be consistent; consider `redirect_slashes=False` |
| `DELETE` returns 204 *and* a body | returned a dict with `status_code=204` | return nothing / `Response(status_code=204)` |
| Second `DELETE` returns 500 | didn't handle "already gone" | `404` is the right answer |
| Crawler deleted rows | a destructive `GET` | never mutate in `GET` |
| `405` where you expected `404` | path matched, verb didn't | usually your test is wrong, not the API |
| `422` you can't explain | Pydantic rejected the body | read `detail[0]["loc"]` and `["msg"]` |
| Two books with the same ISBN | no uniqueness check | `409 Conflict` (Day 06) |
| Ids collide after a delete | `len(books) + 1` as the next id | keep a counter that only increases |
| Concurrent writes lose updates | read-modify-write on a shared list | Day 09's transactions; Day 12's ETags |

## 14. Exercises

1. Implement all six endpoints, then run the `curl` block in section 4 top to
   bottom and check every status code against the table in section 7.
2. Return `409 Conflict` when a `POST` uses an ISBN that already exists. Ask
   yourself why that is not a `422`. (Day 06 answers it.)
3. Add `POST /books/{id}/restock` with `{"quantity": 5}`. Notice it is a verb in
   a URL — decide whether it is justified, and write down your reasoning.
4. Make `PUT /books/{id}` create the book when the id does not exist (`201`) and
   replace it when it does (`200`). This is *upsert*, and it is legal `PUT`.
5. Delete a book twice. Then argue for `404` and for `204` on the second call.
   Both are defensible; pick one and document it.
6. Add `If-Match`-free optimistic locking the crude way: a `version` integer that
   `PUT` must send. Return `409` on a mismatch. Day 12 does this with ETags.
7. Register `/books/` and `/books` deliberately, `POST` to the wrong one with
   `curl -v`, and watch what the redirect does to your body.

## 15. What's next

**[Day 03 — Path and Query Parameters →](../03_path_and_query_parameters/)**
`/books` returns everything, forever. Tomorrow it takes filters, search and
sorting — typed, validated, documented and bounded, using `Path`, `Query` and
`Annotated`.
