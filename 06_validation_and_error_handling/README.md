# Day 06 — Validation and Error Handling

> **Goal:** make every failure look the same — one error envelope, a stable
> machine-readable code, honest status codes, and nothing internal leaking into
> the response.
> **Time:** ~2 hours · **Port:** 8006 · **Builds on:** Day 05

> **Code status:** the README is the spec. Build `shelfspace/` yourself from
> sections 5 onwards before reaching for a reference implementation.

---

## 1. Why this matters

> **Your API has two contracts. You have only written one of them.**

Right now a client integrating with Shelfspace meets three unrelated shapes:

```json
{"detail": "Not Found"}                                     // FastAPI's HTTPException
{"detail": [{"loc": [...], "msg": "...", "type": "..."}]}   // Pydantic's 422
{"detail": "Internal Server Error"}                          // the 500
```

`detail` is a string here and a list there. A client cannot write one parser, so
it writes none, and falls back to matching on the status code and hoping. Then
someone rewords a message and their error handling breaks.

Today you fix that permanently, in about sixty lines.

## 2. What you will build

```
06_validation_and_error_handling/
├── run.py
└── shelfspace/
    ├── config.py
    ├── errors.py       ← the whole lesson: APIError + four handlers
    ├── schemas.py      ErrorResponse, so /docs documents failures too
    ├── data.py
    └── main.py         handlers that raise instead of returning error dicts
```

One envelope, every time:

```json
{
  "error": {
    "status": 422,
    "code": "validation_error",
    "message": "The request body failed validation.",
    "details": {"title": "This field is required.",
                "price": "Must be greater than 0."},
    "request_id": "01J8ZC7Q4K"
  }
}
```

## 3. Run it

```bash
source .venv/bin/activate
cd 06_validation_and_error_handling
python run.py
```

## 4. Try it — learn by doing

```bash
API=http://127.0.0.1:8006
JSON='Content-Type: application/json'

# --- every failure, one shape. Read all of these. ---
curl -s  $API/books/9999                       | python -m json.tool  # 404
curl -sX POST $API/books/1                     | python -m json.tool  # 405
curl -s  $API/books/abc                        | python -m json.tool  # 422 (path)
curl -sX POST $API/books -d 'title=x'          | python -m json.tool  # 415
curl -sX POST $API/books -H "$JSON" -d '{bad'  | python -m json.tool  # 400
curl -sX POST $API/books -H "$JSON" \
  -d '{"title":"","price":"-5"}'                | python -m json.tool # 422 (body)
curl -s  $API/boom                              | python -m json.tool # 500
curl -s  $API/books/1/borrow                    | python -m json.tool # 409

# --- the same `code` regardless of wording ---
curl -s $API/books/9999 | python -c "import json,sys; print(json.load(sys.stdin)['error']['code'])"

# --- 415 vs 400 vs 422: three different client mistakes ---
curl -s -o /dev/null -w '%{http_code}  no content-type\n' -X POST $API/books -d 'x=1'
curl -s -o /dev/null -w '%{http_code}  broken json\n'     -X POST $API/books -H "$JSON" -d '{'
curl -s -o /dev/null -w '%{http_code}  bad values\n'      -X POST $API/books -H "$JSON" -d '{"title":""}'

# --- the 500 tells the client NOTHING, and the log tells you EVERYTHING ---
curl -s $API/boom | python -m json.tool          # generic message + request_id
# now look at your terminal: full traceback, same request_id

# --- validation errors are FLATTENED into field -> message ---
curl -sX POST $API/books -H "$JSON" -d '{
  "isbn":"nope","title":"","price":"-1","stock":-4,
  "author":{"name":"","email":"bad"}}' | python -m json.tool
```

The last one is the payoff: Pydantic's nested `loc` arrays become
`{"author.name": "...", "author.email": "..."}` — a shape a form can consume
directly.

## 5. `HTTPException` — the built-in, and its limits

```python
from fastapi import HTTPException

raise HTTPException(status_code=404, detail="Book not found")
raise HTTPException(status_code=404, detail="Book not found",
                    headers={"X-Error-Code": "book_not_found"})
```

**Raise, never return.** Returning an error dict skips the response model,
returns `200`, and every client treats it as success:

```python
return {"error": "not found"}                  # ❌ HTTP 200. A lie.
raise HTTPException(404, "Book not found")     # ✅
```

`HTTPException` is fine for small apps and runs out for three reasons: `detail`
is free-form so shapes drift; there is no stable machine-readable code; and it
carries no room for field-level details. So you subclass.

## 6. One exception type of your own

```python
# errors.py
class APIError(Exception):
    """Every deliberate failure in this application."""

    def __init__(self, status: int, code: str, message: str,
                 details: dict | None = None):
        self.status = status
        self.code = code            # for MACHINES — stable forever
        self.message = message      # for HUMANS  — may be reworded, translated
        self.details = details or {}
        super().__init__(message)


class NotFound(APIError):
    def __init__(self, resource: str, id_):
        super().__init__(404, f"{resource}_not_found",
                         f"No {resource} with id {id_}.")

class Conflict(APIError):
    def __init__(self, code: str, message: str, **details):
        super().__init__(409, code, message, details)
```

Handlers now read as intent, not plumbing:

```python
book = store.get(book_id)
if book is None:
    raise NotFound("book", book_id)
if book.stock == 0:
    raise Conflict("out_of_stock", "This title is out of stock.", isbn=book.isbn)
```

**`code` vs `message` is the important split.** Clients branch on `code`, display
`message`. Prose gets reworded, shortened, translated — and if a client is
matching on `"Book not found"`, your copy edit is a breaking change.

## 7. Four handlers cover everything

```python
def install_error_handlers(app: FastAPI) -> None:

    @app.exception_handler(APIError)                 # 1. deliberate failures
    async def handle_api_error(request, exc): ...

    @app.exception_handler(RequestValidationError)   # 2. Pydantic on input
    async def handle_validation(request, exc): ...

    @app.exception_handler(StarletteHTTPException)   # 3. 404/405/415 from the framework
    async def handle_http(request, exc): ...

    @app.exception_handler(Exception)                # 4. everything unforeseen
    async def handle_unexpected(request, exc): ...
```

Miss any one of them and a whole class of failure escapes with a different shape:

| Missing | What escapes |
|---|---|
| `APIError` | your own exception becomes a bare `500` |
| `RequestValidationError` | Pydantic's raw `detail` list |
| `StarletteHTTPException` | the framework's own 404/405 |
| `Exception` | an unhandled crash, unlogged, in the framework's format |

> Register the `Exception` handler on the **app**, not just conceptually — and
> note that in development, `debug=True` may still surface the traceback. That is
> what you want locally and must not have in production.

## 8. Flatten Pydantic's errors into something a form can use

Pydantic's raw output is precise and awkward:

```json
{"detail": [{"loc": ["body", "author", "email"],
             "msg": "value is not a valid email address",
             "type": "value_error"}]}
```

Flatten it once, in the handler, for every endpoint:

```python
def flatten(errors: list[dict]) -> dict[str, str]:
    out = {}
    for err in errors:
        loc = [str(p) for p in err["loc"]]
        if loc and loc[0] in {"body", "query", "path", "header", "cookie"}:
            loc = loc[1:]                      # drop the source marker
        out[".".join(loc) or "__root__"] = err["msg"]
    return out
```

`{"author.email": "value is not a valid email address"}` — a UI can map that
straight onto its inputs. Keep the dotted path; a UI with a nested form needs it.

**Return `422`, not `400`, for body validation.** The request parsed fine; the
*values* are wrong. FastAPI already uses `422` — do not "simplify" it to `400`
and lose the distinction.

## 9. Choosing the right status code

| Code | Means | Example here |
|---|---|---|
| `400` | malformed request | broken JSON, undecodable query string |
| `401` | not authenticated | missing/invalid token (Day 15) |
| `403` | authenticated, not allowed | wrong role (Day 16) |
| `404` | no such resource | unknown book id |
| `405` | wrong verb on a real path | `POST /books/1` |
| `409` | conflicts with current state | duplicate ISBN, out of stock |
| `413` | payload too large | 100 MB upload |
| `415` | unsupported media type | no/incorrect `Content-Type` |
| `422` | parsed, values invalid | empty title, negative price |
| `429` | rate limited | Day 13 |
| `500` | **your** bug | unhandled exception |
| `503` | dependency unavailable | database down |

Three distinctions worth internalising:

- **415 vs 400.** 415 = "I do not speak your format" (no or wrong
  `Content-Type`). 400 = "I speak JSON and yours is broken." Collapsing them
  makes a very common client mistake hard to diagnose.
- **400 vs 422.** 400 = malformed. 422 = well-formed, semantically wrong. Field
  errors belong in 422.
- **409 vs 422.** 409 = the payload is fine but conflicts with existing state
  (duplicate ISBN). 422 = the payload itself is invalid. `409` tells the client
  "change the world or try later"; `422` says "fix your input".

And the rule underneath all of it: **`4xx` is the client's fault, `5xx` is
yours.** Returning `400` for your own bug hides real problems from your alerting;
returning `500` for a client's bad input inflates your error rate and pages you
at 3 a.m. for someone else's typo.

## 10. Never leak internals

```python
@app.exception_handler(Exception)
async def handle_unexpected(request: Request, exc: Exception):
    request_id = request.headers.get("X-Request-ID", new_id())
    logger.exception("unhandled error", extra={"request_id": request_id,
                                               "path": request.url.path})
    return JSONResponse(status_code=500, content={"error": {
        "status": 500,
        "code": "internal_error",
        "message": "An unexpected error occurred.",   # nothing else
        "request_id": request_id,
    }})
```

> **Never put `str(exception)` in a response.** Exception text routinely contains
> absolute file paths, SQL fragments with table and column names, connection
> strings, internal hostnames, and occasionally the values that caused the error
> — which may be someone's personal data.

`request_id` is what makes that safe *and* supportable: the client quotes it, you
grep the logs, you get the full traceback without ever having shipped it. Day 13
generates the ID in middleware so every log line in the request carries it.

## 11. Document the failures

```python
# schemas.py
class ErrorDetail(BaseModel):
    status: int
    code: str
    message: str
    details: dict[str, Any] = {}
    request_id: str | None = None

class ErrorResponse(BaseModel):
    error: ErrorDetail

# main.py — one default for the whole app
app = FastAPI(responses={
    400: {"model": ErrorResponse}, 404: {"model": ErrorResponse},
    422: {"model": ErrorResponse}, 500: {"model": ErrorResponse},
})
```

Now `/docs` shows the failure shape on every endpoint, and clients can generate
error types from `/openapi.json` instead of guessing.

Keep a table of your codes in the README of the real project:

| `code` | Status | Meaning |
|---|---|---|
| `book_not_found` | 404 | no book with that id |
| `duplicate_isbn` | 409 | that ISBN already exists |
| `out_of_stock` | 409 | no copies available |
| `validation_error` | 422 | body failed validation |
| `unsupported_media_type` | 415 | send `application/json` |
| `internal_error` | 500 | our fault; quote `request_id` |

That table is the contract. Add to it freely; never repurpose an entry.

## 12. Where validation belongs

Three layers, each with a different job:

| Layer | Checks | Example |
|---|---|---|
| **Schema** (Pydantic) | shape, types, ranges | `price > 0`, ISBN pattern |
| **Business rules** (service, Day 10) | state and invariants | "cannot borrow an out-of-stock book" |
| **Database** | integrity, concurrency | `UNIQUE(isbn)`, foreign keys |

Do **not** collapse them. A schema cannot know the ISBN is taken; a service check
cannot survive two concurrent requests. You need the unique constraint *and* the
friendly `409`:

```python
try:
    store.create(book)
except IntegrityError as exc:              # the database had the last word
    raise Conflict("duplicate_isbn", "A book with this ISBN already exists.",
                   isbn=book.isbn) from exc
```

Checking first and inserting second is a race: two requests both see "free" and
one crashes with a 500. Catch the constraint violation and translate it.

## 13. Best practices introduced today

| Practice | Reason |
|---|---|
| One error envelope for every failure | clients write one parser, not five |
| Stable `code` for machines, prose `message` for humans | rewording must not break clients |
| Raise, never return, errors | a returned dict is an HTTP 200 that lies |
| One `APIError` base + small subclasses | handlers read as intent |
| Register all four exception handlers | each covers a class of failure |
| Flatten validation errors to `field → message` | a form can consume it directly |
| Distinguish 400 / 409 / 415 / 422 | each names a different client mistake |
| `4xx` = client, `5xx` = you | your alerting depends on the split |
| Report **all** validation errors at once | one round trip, not five |
| Never include exception text in a response | it leaks paths, SQL and internals |
| Log the traceback with a `request_id` | supportable without disclosure |
| Document `ErrorResponse` in `responses={}` | failures are part of the contract |
| Translate `IntegrityError` into `409` | check-then-insert is a race |
| Keep a code table and never repurpose a code | it is a published contract |

## 14. Common mistakes

| Symptom | Cause | Fix |
|---|---|---|
| Client treats an error as success | returned an error dict | `raise` |
| Three different error shapes | missing handlers | register all four |
| An HTML error page reaches a client | no `StarletteHTTPException` handler | add one |
| Client matches on message text | no stable `code` | add codes and document them |
| A copy edit broke a client | they matched on prose | that is why `code` exists |
| Stack trace in the response body | `str(exc)` in the payload | log it; return a generic message |
| Error rate spikes from bad input | client errors returned as 500 | use `4xx` |
| Real bugs invisible in dashboards | bugs returned as 400 | `500` for your faults |
| Support cannot trace a report | no `request_id` | generate and return one |
| One field fixed per round trip | validation returns on first error | collect all |
| Nested errors unusable in a UI | raw `loc` arrays | flatten to dotted paths |
| 500 on a duplicate ISBN | uncaught `IntegrityError` | catch → `409` |
| Duplicate rows despite a check | check-then-insert race | rely on the DB constraint |
| `422` "simplified" to `400` | thought they were the same | they are not |
| Missing `Content-Type` gives 422 | conflated with validation | `415` |
| Handler never fires | registered on a router, not the app | exception handlers are app-level |

## 15. Exercises

1. Build `errors.py` with `APIError`, `NotFound`, `Conflict` and all four
   handlers, then run the whole `curl` block in section 4 and confirm every
   response has an `error` object with the same five keys.
2. Add `request_id` to every error and log it with the traceback. Trigger
   `/boom`, then find the traceback in your terminal by the id in the response.
3. Write the flattening function and prove it with a nested validation error
   three levels deep.
4. Add a `415` handler: reject a `POST` without `Content-Type: application/json`
   before Pydantic ever runs.
5. Add `duplicate_isbn` two ways — a pre-check and an `IntegrityError` catch.
   Then reason about which one survives two simultaneous requests.
6. Add a `Retry-After` header to a `503` and explain what a well-behaved client
   should do with it.
7. Localise `message` by `Accept-Language` while keeping `code` fixed. Notice
   this is only possible *because* they are separate.
8. Write tests asserting the envelope for 404, 405, 415, 422, 409 and 500. That
   test file is your error contract — Day 17 formalises it.

## 16. What's next

**[Day 07 — Project Structure and Routers →](../07_project_structure_and_routers/)**
`main.py` is now carrying routes, error handlers, configuration and data access.
Tomorrow it gets broken into routers and layers — the structure that lets an app
grow past a hundred endpoints without becoming unnavigable.
