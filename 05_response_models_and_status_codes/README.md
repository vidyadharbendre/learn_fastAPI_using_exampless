# Day 05 — Response Models and Status Codes

> **Goal:** control what leaves your server — a response model that cannot leak,
> an inheritance chain that kills yesterday's duplication, and the right status
> code and headers for every operation.
> **Time:** ~2 hours · **Port:** 8005 · **Builds on:** Day 04

> **Code status:** the README is the spec. Build `shelfspace/` yourself from
> sections 5 onwards before reaching for a reference implementation.

---

## 1. Why this matters

> **Every data breach you have read about was a response that contained one
> field too many.**

Nobody plans to return `password_hash`. What happens is: an ORM object is
returned directly, a column is added six months later by someone working on
authentication, and now every `GET /users` ships it. No code changed in the
endpoint. No test failed. The leak arrived by inheritance.

A `response_model` makes that impossible — it is an allow-list, and a field that
is not declared is not sent, no matter what the handler returns.

## 2. What you will build

Output contracts for the whole catalogue, plus a user resource that exists purely
to have something worth leaking:

```
05_response_models_and_status_codes/
├── run.py
└── shelfspace/
    ├── config.py
    ├── data.py
    ├── schemas.py
    │   ├── BookBase        shared fields — the base of the chain
    │   ├── BookCreate      input  (Day 04)
    │   ├── BookPublic      output: + id, created_at
    │   ├── BookDetail      output: + author object, related books
    │   ├── UserInternal    has password_hash — must never ship
    │   ├── UserPublic      what the world sees
    │   └── Page[T]         a generic envelope
    └── main.py
```

## 3. Run it

```bash
source .venv/bin/activate
cd 05_response_models_and_status_codes
python run.py
```

In <http://127.0.0.1:8005/docs>, each endpoint now shows **two** schemas —
request and response — and the response section lists every status code you
declared, with an example body. That page is now a usable API reference.

## 4. Try it — learn by doing

```bash
API=http://127.0.0.1:8005
JSON='Content-Type: application/json'

# --- the leak that does not happen ---
curl -s $API/users/1 | python -m json.tool        # no password_hash, ever
# now read main.py: the handler returns the FULL internal object

# --- 201 + Location + the created resource ---
curl -isX POST $API/books -H "$JSON" -d '{
  "isbn":"978-1-4919-4600-8","title":"Fluent Python",
  "price":"4199.00","stock":7,"published_year":2022,
  "author_id":1}' | head -14

# --- summary vs detail: two shapes, one resource ---
curl -s $API/books        | python -m json.tool | head -20   # BookPublic
curl -s $API/books/1      | python -m json.tool              # BookDetail

# --- fields the server assigns are absent on input, present on output ---
curl -sX POST $API/books -H "$JSON" -d '{
  "isbn":"978-0-13-235088-4","title":"Clean Code","price":"3500.00",
  "stock":2,"published_year":2008,"author_id":1,
  "id":9999,"created_at":"1999-01-01T00:00:00Z"}' | python -m json.tool
# id is NOT 9999 — the input model never accepted it

# --- 204 really means no body ---
curl -isX DELETE $API/books/3 | head -4
curl -s $API/books/3 -o /dev/null -w '%{http_code}\n'

# --- response validation is server-side, and it fails LOUDLY ---
curl -si $API/books/broken | head -3      # 500: the handler broke its own contract

# --- null-heavy payloads, trimmed ---
curl -s "$API/books/1"                       | python -m json.tool
curl -s "$API/books/1?compact=true"          | python -m json.tool   # exclude_none

# --- a non-JSON response, declared properly ---
curl -si $API/books/1/cover  | head -5      # image/png, documented as such
curl -s  $API/books.csv      | head -3      # text/csv
```

The first block is the day in one command: the handler returns everything, the
client receives only what the contract allows.

## 5. `response_model` is a filter, not a comment

```python
@app.get("/users/{user_id}", response_model=UserPublic)
async def get_user(user_id: int) -> UserPublic:
    return db_get_user(user_id)      # returns password_hash, internal_notes, …
```

FastAPI takes whatever you return, validates it against `UserPublic`, and
**serialises only the declared fields**. Extra attributes are dropped. This is
not documentation that hopes to match behaviour — it *is* the behaviour.

You can declare it two ways:

```python
async def get_user(...) -> UserPublic:                       # return annotation
@app.get("/users/{id}", response_model=UserPublic)           # decorator argument
```

The return annotation is enough on its own and reads better. Use the decorator
argument when the annotation cannot express it — a union of models, a
`response_model=None` escape hatch, or when the function genuinely returns
`Response`.

**Response validation runs on every request.** If your handler returns something
that does not fit, the client gets a `500` — not a malformed payload. That is the
right trade: a loud server error beats a silently broken contract that a client
parses into garbage.

## 6. Inheritance kills the duplication

Day 04 ended with four near-identical classes. Fix it with a base:

```python
class BookBase(BaseModel):                 # everything a client may send
    isbn: str
    title: str
    price: Decimal
    stock: int = 0

class BookCreate(BookBase):                # input: nothing added
    author_id: int

class BookPublic(BookBase):                # output: + server-assigned fields
    id: int
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)

class BookDetail(BookPublic):              # output: + relations
    author: AuthorPublic
    related: list[BookPublic] = []

class BookPatch(BaseModel):                # every field optional — do NOT inherit
    title: str | None = None
    price: Decimal | None = None
    stock: int | None = None
```

Two rules that stop this becoming clever-but-wrong:

- **Never inherit output from input or vice versa.** They diverge — `price`
  becomes a computed field on output, or input gains a `terms_accepted` flag —
  and a shared base that has to serve both gets contorted.
- **`BookPatch` does not inherit.** Making required fields optional through
  inheritance means overriding every field anyway; write it out.

`from_attributes=True` (v1's `orm_mode`) lets Pydantic read a SQLAlchemy row
object, not just a dict. You need it from Day 09 — put it on output models now.

## 7. Status codes, and where to set them

```python
@app.post("/books", status_code=201)                 # the default for this route
@app.delete("/books/{id}", status_code=204)

async def create(...):
    ...
    response.status_code = 200          # a per-request override, when needed
```

| Code | Use | The trap |
|---|---|---|
| `200 OK` | reads, `PUT`, `PATCH` | the default — so `201`/`204` need saying |
| `201 Created` | `POST` that creates | needs a `Location` header |
| `202 Accepted` | queued work (Day 18) | say **where to poll** in the body |
| `204 No Content` | `DELETE` | body must be **empty** |
| `304 Not Modified` | conditional GET (Day 12) | body must be empty too |
| `404` / `409` / `422` | Day 06 | each names a different client mistake |

Prefer the `http.HTTPStatus` constants (`status.HTTP_201_CREATED` in FastAPI) over
bare integers once you have more than a handful of routes — `204` and `202` are
easy to transpose and impossible to grep for.

**`204` and `Response`.** Returning a model with `status_code=204` makes FastAPI
try to serialise a body into a response that must not have one. Return
`Response(status_code=204)` and annotate `response_class=Response`, so `/docs`
does not advertise a body that never arrives.

## 8. Headers: `Location`, and the rest

```python
@app.post("/books", status_code=201, response_model=BookPublic)
async def create_book(payload: BookCreate, request: Request, response: Response):
    book = store.create(payload)
    response.headers["Location"] = str(request.url_for("get_book", book_id=book.id))
    return book
```

Injecting `Response` gives you headers and status without giving up the response
model — you still return the object, FastAPI still filters it. Build the URL with
`url_for` so a path change cannot desynchronise the header.

Other headers worth setting deliberately:

| Header | Why |
|---|---|
| `Location` | on every `201` — the client should never construct the URL |
| `Cache-Control` | `no-store` on anything user-specific; `public, max-age=…` on catalogues |
| `ETag` | enables `304` and optimistic locking (Day 12) |
| `X-Request-ID` | correlates a client report with your logs (Day 13) |
| `X-Content-Type-Options: nosniff` | stops a browser treating JSON as HTML |

## 9. Documenting more than the happy path

```python
@app.get(
    "/books/{book_id}",
    response_model=BookDetail,
    responses={
        404: {"model": ErrorResponse, "description": "No book with that id"},
        304: {"description": "Not modified"},
    },
)
```

`/docs` shows only the success schema unless you say otherwise, and the error
shapes are the half clients actually struggle with. Day 06 gives you one
`ErrorResponse` model to point every entry at.

For an endpoint that can return genuinely different models:

```python
@app.get("/books/{id}", response_model=BookDetail | BookPublic)
```

Order matters — Pydantic tries the union members left to right and uses the first
that validates, so put the most specific model first.

## 10. Trimming the payload

```python
@app.get("/books/{id}", response_model=BookPublic,
         response_model_exclude_none=True,      # drop nulls
         response_model_exclude={"internal_ref"})  # drop specific fields
```

| Option | Effect | Use when |
|---|---|---|
| `response_model_exclude_none` | omits `null` fields | sparse records; mobile payloads |
| `response_model_exclude_unset` | omits fields never explicitly set | `PATCH` echoes |
| `response_model_exclude_defaults` | omits values equal to the default | rarely — surprising |
| `response_model_include` / `exclude` | field allow/deny list | one endpoint's variation |

Be careful: **omitting a key is not the same as sending `null`**, and clients
written against one behaviour break on the other. Pick per endpoint, document it,
and do not toggle it globally later.

If different callers need different field sets, prefer explicit models
(`BookPublic` vs `BookDetail`) or sparse fieldsets (`?fields=`) over
`exclude_none` — the shape stays predictable.

## 11. Computed and renamed fields

```python
class BookPublic(BookBase):
    id: int
    stock: int = Field(exclude=True)          # used to compute, never shipped

    @computed_field
    @property
    def in_stock(self) -> bool:
        return self.stock > 0

    @computed_field
    @property
    def display_price(self) -> str:
        return f"₹{self.price:,.2f}"
```

`@computed_field` puts derived values in the response **and** in the OpenAPI
schema, so every client stops re-implementing the same rule slightly differently.

For renaming on the way out:

```python
class BookPublic(BaseModel):
    published_year: int = Field(serialization_alias="publishedYear")
    model_config = ConfigDict(populate_by_name=True)
```

`serialization_alias` changes the wire format only; your Python stays snake_case.
Pick one convention for the whole API — mixed `camelCase` and `snake_case` in one
payload is the sign of an API assembled by three people who never spoke.

## 12. When the response is not JSON

```python
@app.get("/books/{id}/cover", response_class=Response,
         responses={200: {"content": {"image/png": {}}}})
async def cover(id: int) -> Response:
    return Response(content=png_bytes, media_type="image/png")

@app.get("/books.csv", response_class=PlainTextResponse)     # text/csv
@app.get("/books/{id}/download")                             # FileResponse
@app.get("/reports/large")                                   # StreamingResponse (Day 19)
```

Two things break here if you are careless: `response_model` does not apply to a
raw `Response` (you are past the serialisation layer), and `/docs` will keep
claiming `application/json` unless you declare the content type in `responses`.

`JSONResponse` directly is a last resort — it skips your response model entirely.
When you find yourself reaching for it, ask whether a `Response` parameter plus a
model would do the job.

## 13. Best practices introduced today

| Practice | Reason |
|---|---|
| `response_model` on **every** endpoint | it is an allow-list; leaks become impossible |
| Never return an ORM object unfiltered | tomorrow's new column ships itself |
| Separate input and output models | they diverge; a shared base gets contorted |
| Share fields via a `Base`, not by reusing a model | removes duplication without coupling |
| `from_attributes=True` on output models | reads ORM rows directly from Day 09 |
| Set `status_code` explicitly per route | the default `200` is silently wrong for POST/DELETE |
| `201` + `Location`, always together | the client never guesses a URL |
| `204` with a real `Response`, no body | serialising into a 204 is a protocol violation |
| Build URLs with `url_for` | route paths change; names do not |
| Document error responses in `responses={}` | the failure shapes are what clients get wrong |
| `@computed_field` for derived values | one rule, not one per client |
| One naming convention across the API | mixed cases are a smell clients pay for |
| Declare the media type for non-JSON | `/docs` must not promise JSON |
| Prefer distinct models over `exclude_*` flags | a stable shape beats a configurable one |

## 14. Common mistakes

| Symptom | Cause | Fix |
|---|---|---|
| `password_hash` in a response | no `response_model` | declare one; it filters |
| A new column leaked into the API | returning the ORM object | filter through a model |
| `500` after a refactor | handler no longer matches the response model | fix the handler — the check is working |
| `id: int \| None` in every model | one model for input and output | split them |
| `POST` returns `200` | forgot `status_code=201` | set it |
| `204` response carries a body | returned a model with `status_code=204` | `Response(status_code=204)` |
| `/docs` shows a body for a 204 | no `response_class=Response` | declare it |
| `Location` points at the wrong path | hand-built f-string | `request.url_for(...)` |
| Client cannot parse an error | only the success schema documented | fill in `responses={}` |
| A field vanishes intermittently | `response_model_exclude_none` | send `null`, or document the omission |
| `from_attributes` error at runtime | model reading an ORM object without it | add `ConfigDict(from_attributes=True)` |
| Union response returns the wrong shape | union order | most specific model first |
| `JSONResponse` bypassed the model | returned it directly | return the object; use `Response` for headers |
| `camelCase` and `snake_case` mixed | inconsistent aliases | one convention, applied everywhere |
| Huge payloads on list endpoints | `BookDetail` used for lists | summary model for lists, detail for one |
| Timestamps without an offset | naive `datetime` on the model | UTC-aware datetimes (Day 01) |

## 15. Exercises

1. Give `UserInternal` a `password_hash`, return it straight from the handler
   behind `response_model=UserPublic`, and confirm with `curl` that it never
   ships. Then delete the `response_model` and look again.
2. Build the `BookBase → BookCreate / BookPublic → BookDetail` chain and use the
   summary model on `/books`, the detail model on `/books/{id}`. Compare payload
   sizes for 100 books.
3. Add `@computed_field` `in_stock` and `display_price`, and check they appear in
   `/openapi.json`.
4. Implement `DELETE` correctly: `204`, empty body, `response_class=Response`,
   and `/docs` that does not promise a payload.
5. Break the contract on purpose — return `{"id": "abc"}` from a handler declared
   `-> BookPublic` — and read the 500 and the server log. Explain why a 500 is
   the right answer.
6. Add `GET /books.csv` with the correct media type, and confirm `/docs` shows
   `text/csv` rather than JSON.
7. Add a generic `Page[T]` envelope (`items`, `total`, `limit`, `offset`) and use
   it for `/books`. Day 12 builds on it.
8. Turn on `serialization_alias` camelCase for one model, then read
   `/openapi.json` and decide whether you would apply it to the whole API.

## 16. What's next

**[Day 06 — Validation and Error Handling →](../06_validation_and_error_handling/)**
Your successes now have a contract. Your failures do not: FastAPI's `{"detail":
...}`, Pydantic's `422` list and an unhandled exception's HTML-free 500 are three
different shapes. Tomorrow they become one envelope with a stable, machine-readable
error code.
