# Day 03 — Path and Query Parameters

> **Goal:** let clients ask precise questions — filter, search, sort, paginate —
> with every input typed, validated, bounded and documented before your code
> sees it.
> **Time:** ~2 hours · **Port:** 8003 · **Builds on:** Day 02

> **Code status:** the README is the spec. Build `shelfspace/` yourself from
> sections 5 onwards before reaching for a reference implementation.

---

## 1. Why this matters

> **Every query parameter is a public input from an anonymous stranger.**

`?limit=1000000` is not a typo, it is a denial-of-service you invited.
`?sort=price; DROP TABLE books` is not paranoia, it is Tuesday. And
`?page=abc` crashing with a `ValueError` deep in your code is a 500 where a
`422` was owed.

FastAPI can reject all three **before your function is called** — but only if you
declare what you accept. Today is about declaring it properly.

## 2. What you will build

A catalogue endpoint clients can actually use:

```
03_path_and_query_parameters/
├── run.py
└── shelfspace/
    ├── config.py
    ├── data.py         filter/sort helpers over the in-memory catalogue
    ├── params.py       reusable Annotated parameter types
    ├── schemas.py      Book, BookPage, SortField (an Enum)
    └── main.py         the routes
```

```
GET /books?q=python&min_price=100&max_price=5000&in_stock=true
          &sort=-price&limit=20&offset=0
GET /books/{book_id}                       # int, ≥ 1
GET /books/isbn/{isbn}                     # validated by regex
GET /authors/{author}/books                # path + query together
GET /files/{file_path:path}                # a path that contains slashes
```

## 3. Run it

```bash
source .venv/bin/activate
cd 03_path_and_query_parameters
python run.py
```

Open <http://127.0.0.1:8003/docs>: every parameter appears with its type, its
bounds, its default and its description — a form you can fill in, generated from
the annotations.

## 4. Try it — learn by doing

```bash
API=http://127.0.0.1:8003

# --- path parameters are typed and coerced ---
curl -s  $API/books/1        | python -m json.tool     # int, fine
curl -s  $API/books/abc      | python -m json.tool     # 422: "not a valid integer"
curl -s  $API/books/0        | python -m json.tool     # 422: ge=1 violated
curl -s  $API/books/-1       | python -m json.tool     # 422, not a 404

# --- query parameters: optional, typed, bounded ---
curl -s "$API/books"                          | python -m json.tool | head
curl -s "$API/books?q=python"                 | python -m json.tool
curl -s "$API/books?min_price=1000&max_price=5000" | python -m json.tool
curl -s "$API/books?in_stock=true"            | python -m json.tool
curl -s "$API/books?sort=-price&limit=3"      | python -m json.tool

# --- booleans accept what humans type ---
for v in true True 1 yes on false 0 no off; do
  printf '%-6s -> ' "$v"; curl -s -o /dev/null -w '%{http_code}\n' "$API/books?in_stock=$v"
done
curl -s "$API/books?in_stock=maybe" | python -m json.tool     # 422

# --- the guard rails ---
curl -s "$API/books?limit=999999"  | python -m json.tool   # 422: le=100
curl -s "$API/books?limit=0"       | python -m json.tool   # 422: ge=1
curl -s "$API/books?sort=colour"   | python -m json.tool   # 422 + allowed values
curl -s "$API/books?min_price=abc" | python -m json.tool   # 422, never a 500

# --- lists: repeat the key, don't comma-separate ---
curl -s "$API/books?tag=classic&tag=fiction" | python -m json.tool

# --- unknown parameters are IGNORED, silently ---
curl -s "$API/books?limitt=5" | python -m json.tool        # typo → default limit

# --- a path parameter that contains slashes ---
curl -s "$API/files/covers/2024/odyssey.jpg" | python -m json.tool
```

Run the boolean loop and the typo line and sit with both results. One is FastAPI
being generous; the other is FastAPI being silent in a way that will cost someone
an afternoon (section 11).

## 5. Path vs query — which is which

| | Path | Query |
|---|---|---|
| Looks like | `/books/42` | `/books?limit=20` |
| Identifies | **which** resource | **how** to present the collection |
| Optional? | never | usually |
| Missing value | `404` (no route matched) | the default applies |
| Belongs in | required identity | filters, sorting, paging, flags |

```
✅ GET /books/42                 the book with id 42
✅ GET /books?author_id=42       books filtered by author
❌ GET /books?id=42              identity smuggled into a filter
❌ GET /books/filter/price/100   a query string wearing a costume
```

A path parameter is part of the resource's **name**. If removing it would leave a
sentence that still means something ("all the books"), it is a query parameter.

## 6. `Annotated` is the modern spelling

```python
from typing import Annotated
from fastapi import Query, Path

# ✅ current
async def list_books(limit: Annotated[int, Query(ge=1, le=100)] = 20): ...

# ⚠️ legacy — still works, appears in older tutorials
async def list_books(limit: int = Query(20, ge=1, le=100)): ...
```

Prefer `Annotated` for three concrete reasons:

1. The **default stays where defaults live** (`= 20`), so the signature reads
   like normal Python.
2. The annotation is reusable — `PageLimit = Annotated[int, Query(ge=1, le=100)]`
   can be shared by ten endpoints, and the legacy form cannot.
3. Type checkers understand it. In the legacy form, `limit`'s declared type is
   `int` while its default is a `Query` object, and mypy is right to complain.

Reusable parameter types are the payoff, and why `params.py` exists:

```python
# params.py
SearchQuery = Annotated[str | None, Query(max_length=100, description="Full-text search")]
Limit       = Annotated[int, Query(ge=1, le=100, description="Page size (max 100)")]
Offset      = Annotated[int, Query(ge=0, description="Rows to skip")]
BookId      = Annotated[int, Path(ge=1, description="Book id")]
```

Define the bound **once**; every endpoint that imports it is bounded correctly.

## 7. Required, optional, and the difference `None` makes

```python
q: str                          # required — omitting it is a 422
q: str = "python"               # optional, defaults to "python"
q: str | None = None            # optional, absent is a real, checkable state
q: Annotated[str | None, Query()] = None    # same, with room for validation
```

The third form is the one to reach for on filters. `None` means *the client did
not ask*, which is genuinely different from an empty string:

```python
if q is not None:                  # ✅ filter only when asked
    books = [b for b in books if q.lower() in b["title"].lower()]
```

Using `q: str = ""` collapses "no filter" and "search for nothing" into one
value, and you will eventually need to tell them apart.

## 8. Validation you get by declaring it

| Constraint | Applies to | Example |
|---|---|---|
| `ge` / `gt` / `le` / `lt` | numbers | `Query(ge=1, le=100)` |
| `min_length` / `max_length` | strings | `Query(max_length=100)` |
| `pattern` | strings | `Path(pattern=r"^\d{3}-\d-\d{2,5}-\d{2,7}-\d$")` |
| `Enum` type | any | `sort: SortField` |
| `Literal` type | any | `order: Literal["asc", "desc"]` |

Two of these deserve emphasis.

**`le` on every limit.** An unbounded `?limit=` is a way for a stranger to ask
your database for five million rows. Cap it in the annotation, where the cap is
also documented and cannot be forgotten in one handler.

**An `Enum` for sortable fields.** This is not tidiness — it is the difference
between an injection surface and a closed set:

```python
class SortField(str, Enum):
    title = "title"
    price = "price"
    stock = "stock"

async def list_books(sort: SortField = SortField.title): ...
```

Now `?sort=colour` is a `422` listing the permitted values, `?sort=price;DROP…`
never reaches your query builder, and `/docs` renders a dropdown. Never
interpolate a raw query parameter into SQL or `getattr` — Day 12 revisits this as
an allow-list.

## 9. Types FastAPI parses for you

```python
published_after: date | None = None      # ?published_after=2024-01-01
created_at: datetime | None = None       # ISO-8601, offset preserved
book_uuid: UUID                          # rejects a malformed UUID with 422
in_stock: bool = False                   # true/True/1/yes/on → True
tag: Annotated[list[str], Query()] = []  # ?tag=a&tag=b
price: Decimal | None = None             # exact, unlike float
```

Three notes from real use:

- **Booleans are generous.** `true`, `True`, `1`, `yes`, `on` all parse (and
  their negatives). `?in_stock` with no value does **not** — it is an empty
  string, and empty is not a boolean. Clients must send `?in_stock=true`.
- **Lists repeat the key.** `?tag=a&tag=b`, not `?tag=a,b`. If you need the
  comma form, take a `str` and split it yourself — and document it.
- **Use `Decimal` for money, never `float`.** Day 01 sends money as a string for
  the same reason.

## 10. Pagination: `limit`/`offset` today, cursors later

```python
async def list_books(limit: Limit = 20, offset: Offset = 0) -> BookPage:
    total = len(matches)
    return BookPage(items=matches[offset:offset + limit],
                    total=total, limit=limit, offset=offset)
```

Return **`total` alongside the page**, always. Without it a client cannot render
"page 3 of 12", cannot know when to stop, and will keep requesting pages until it
gets an empty one — doubling your query volume to learn something you already
knew.

`limit`/`offset` is simple and has a known flaw: on a table being written to,
`OFFSET 10000` is both slow and *unstable* — a row inserted between requests
shifts everything, so an item can appear on two consecutive pages or on neither.
Day 12 fixes it with cursors. Today, know the flaw.

## 11. Unknown parameters are ignored — plan for it

```bash
curl "$API/books?limitt=5"       # 200 OK, limit is 20. No warning anywhere.
```

FastAPI ignores query parameters it does not recognise, and that is correct HTTP
behaviour — it is how you add a parameter without breaking old clients. It is
also how `?limt=5` silently returns the wrong page for a week.

If an endpoint should be strict, validate explicitly:

```python
KNOWN = {"q", "min_price", "max_price", "in_stock", "sort", "limit", "offset"}

unknown = set(request.query_params) - KNOWN
if unknown:
    raise HTTPException(400, f"Unknown query parameters: {sorted(unknown)}")
```

Be deliberate: strictness catches typos and breaks clients that append their own
tracking parameters. Public APIs usually ignore; internal ones often reject.

## 12. Combining path and query

```python
@app.get("/authors/{author}/books")
async def books_by_author(
    author: Annotated[str, Path(min_length=1)],
    sort: SortField = SortField.title,
    limit: Limit = 20,
) -> BookPage:
```

FastAPI decides where each parameter comes from by name: it matches the path
first, then treats the rest as query parameters (and, from Day 04, request
bodies). Nothing is positional, so the order in the signature is free.

**A path that contains slashes** needs the `:path` converter:

```python
@app.get("/files/{file_path:path}")     # /files/covers/2024/odyssey.jpg
```

If you ever join that value onto a real filesystem path, treat it as hostile —
`../../etc/passwd` is the oldest trick there is. Day 19 covers doing it safely.

## 13. Best practices introduced today

| Practice | Reason |
|---|---|
| Path = identity, query = presentation | the URL should read like a sentence |
| `Annotated[...]` over the legacy default form | defaults stay defaults; types stay honest |
| Share parameter types from one module | one place to change a bound |
| `le=` on every limit | uncapped paging is a DoS you shipped |
| `ge=1` on ids | `/books/-1` is a 422, not a database round trip |
| `Enum` or `Literal` for closed sets | injection-proof, self-documenting, a dropdown in `/docs` |
| `str \| None = None` for filters | "not asked" ≠ "empty" |
| `description=` on every parameter | `/docs` becomes the API reference |
| Return `total` with every page | clients can't paginate blind |
| `Decimal` for money, never `float` | binary floats cannot hold 0.10 |
| Know that unknown params are ignored | typos fail silently by design |
| Validate at the edge, not in the handler | the handler receives known-good values |
| `{path:path}` only with sanitisation | `../` traversal is trivially attempted |

## 14. Common mistakes

| Symptom | Cause | Fix |
|---|---|---|
| Filter never applies | `if q:` skips `""` and `0` | test `if q is not None` |
| `?limit=1000000` melts the database | no upper bound | `Query(le=100)` |
| 500 on `?page=abc` | parsed by hand with `int()` | annotate the type; get a 422 |
| `?sort=` crashes or injects | raw value used in the query | `Enum` allow-list |
| Typo'd parameter silently ignored | correct HTTP behaviour | reject explicitly if it matters |
| `?tag=a,b` arrives as one string | lists repeat the key | `?tag=a&tag=b`, or split by hand |
| `?in_stock` alone gives a 422 | empty string is not a boolean | send `?in_stock=true` |
| Static route shadowed by `/{id}` | declaration order | static routes first (Day 02) |
| `/books/abc` returns 404, not 422 | path type is `str` | annotate it as `int` |
| Same item on two pages | offset paging over a changing table | cursors (Day 12) |
| Client fetches until an empty page | no `total` in the response | include it |
| Dates arrive as strings | annotated as `str` | annotate `date` / `datetime` |
| `?q=%20%20` matches everything | no trimming or min length | `min_length=1` and `.strip()` |
| Mypy complains about `Query(...)` defaults | legacy parameter form | switch to `Annotated` |
| Path traversal via `{path:path}` | joined onto the filesystem raw | resolve and confine to a root (Day 19) |

## 15. Exercises

1. Implement `/books` with `q`, `min_price`, `max_price`, `in_stock`, `sort`,
   `limit`, `offset` — then verify every guard-rail line in section 4 returns a
   `422` with a readable message.
2. Add `?fields=id,title` (sparse fieldsets) and decide how to report an unknown
   field name.
3. Make `min_price > max_price` a `422`. Note that no single-field constraint can
   express this — you need a cross-field check. Day 04 shows the clean way
   (`model_validator`); do it crudely first so you feel the gap.
4. Move `limit`/`offset` into a shared dependency so ten endpoints paginate
   identically. (You are inventing Day 08 — good.)
5. Support descending sort as `?sort=-price` and reject `--price`. Compare that
   with `?sort=price&order=desc` and write down which you would ship.
6. Add strict unknown-parameter rejection behind a setting
   (`SHELFSPACE_STRICT_QUERY=true`) and argue when you would enable it.
7. Add `?published_after=2024-01-01` as a `date`, then send `2024-13-45` and read
   the error. You wrote no date parsing.

## 16. What's next

**[Day 04 — Request Bodies and Pydantic Models →](../04_request_bodies_and_pydantic_models/)**
Query parameters are flat strings. Tomorrow the client sends structured JSON —
nested objects, lists, custom validators, and models that make an invalid book
impossible to construct.
