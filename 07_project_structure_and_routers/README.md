# Day 07 — Project Structure and Routers

> **Goal:** break a growing `main.py` into routers and layers that survive a
> hundred endpoints and three more developers — without inventing architecture
> you do not need.
> **Time:** ~2 hours · **Port:** 8007 · **Builds on:** Day 06

> **Code status:** the README is the spec. Build `shelfspace/` yourself from
> sections 5 onwards before reaching for a reference implementation.

---

## 1. Why this matters

> **Nobody decides to write a 2,000-line `main.py`. It arrives one reasonable
> endpoint at a time.**

Every addition is small, so nobody stops to reorganise. Then merge conflicts
happen in one file, nobody can find anything, and a change to book listing
touches the same lines as a change to authentication.

The counter-failure is worse in a different way: seven layers of abstraction over
three endpoints, where adding a field means editing five files. Today is about
the middle — **structure proportional to the application**, and knowing which
signals justify the next step.

## 2. What you will build

The same API as Day 06, restructured:

```
07_project_structure_and_routers/
├── run.py
└── shelfspace/
    ├── __init__.py
    ├── main.py              create_app() — assembly only, ~40 lines
    ├── core/
    │   ├── config.py        Settings (+ per-environment overrides)
    │   ├── errors.py        APIError and the handlers (Day 06)
    │   └── logging.py       one place that configures logging
    ├── api/
    │   ├── __init__.py      api_router — one router to include
    │   ├── deps.py          shared dependencies (Day 08 fills this in)
    │   └── v1/
    │       ├── books.py     /api/v1/books
    │       ├── authors.py   /api/v1/authors
    │       └── health.py    /health  (deliberately unversioned)
    ├── schemas/
    │   ├── book.py
    │   └── author.py
    ├── services/
    │   └── catalogue.py     business rules, no HTTP types
    └── repositories/
        └── books.py         data access, swapped for SQLAlchemy on Day 09
```

`main.py` shrinks to assembly: build settings, create the app, install error
handlers, include one router.

## 3. Run it

```bash
source .venv/bin/activate
cd 07_project_structure_and_routers
python run.py
```

```bash
curl -s http://127.0.0.1:8007/api/v1/books | python -m json.tool
curl -s http://127.0.0.1:8007/health       | python -m json.tool
```

Open `/docs`: endpoints are grouped by tag, and the URL prefix is set in exactly
one place per router.

## 4. Try it — learn by doing

```bash
API=http://127.0.0.1:8007

# --- versioned business endpoints, unversioned operational ones ---
curl -s $API/api/v1/books      | python -m json.tool | head
curl -s $API/api/v1/authors    | python -m json.tool | head
curl -s $API/health            | python -m json.tool

# --- one prefix change moves every route in a file ---
#   books.py:  router = APIRouter(prefix="/books", tags=["catalogue"])
#   api/__init__.py: api_router.include_router(books.router)
#   main.py:   app.include_router(api_router, prefix="/api/v1")
# change the last one to /api/v2 and re-run:
curl -s $API/api/v1/books -o /dev/null -w 'v1=%{http_code}\n'

# --- the routing table, printed ---
curl -s $API/openapi.json | python -c "
import json,sys
for p, ops in sorted(json.load(sys.stdin)['paths'].items()):
    print(f'{\" \".join(sorted(m.upper() for m in ops)):<22} {p}')"

# --- error handling still works: it is installed on the app, not a router ---
curl -s $API/api/v1/books/9999 | python -m json.tool

# --- per-environment settings ---
SHELFSPACE_ENVIRONMENT=production python run.py &   # then:
curl -s -o /dev/null -w 'docs in prod = %{http_code}\n' $API/docs
```

## 5. `APIRouter` — a router is a mini-application

```python
# api/v1/books.py
from fastapi import APIRouter

router = APIRouter(
    prefix="/books",
    tags=["catalogue"],
    responses={404: {"model": ErrorResponse}},
)

@router.get("", response_model=BookPage)
async def list_books(...): ...

@router.get("/{book_id}", response_model=BookDetail, name="get_book")
async def get_book(book_id: int): ...
```

Everything `@app` supports, `@router` supports. Then it is composed:

```python
# api/__init__.py
api_router = APIRouter()
api_router.include_router(books.router)
api_router.include_router(authors.router)

# main.py
app.include_router(api_router, prefix="/api/v1")
app.include_router(health.router)          # no version prefix
```

Prefixes stack: `/api/v1` + `/books` + `/{book_id}`. Set the version prefix in
**one** place so a `v2` is an inclusion change, not a find-and-replace.

Two details that bite:

- **Use `@router.get("")`, not `@router.get("/")`,** for the collection root when
  the router has a prefix — otherwise the path becomes `/books/` and you inherit
  Day 02's trailing-slash redirect.
- **Name your routes** (`name="get_book"`) when anything calls `url_for`. The
  default name is the function name, which is fine until two routers both define
  `get_book` — then the name is ambiguous and `url_for` picks the wrong one.

## 6. Layers, and what each one is forbidden to know

| Layer | Job | Must not know about |
|---|---|---|
| **Router** (`api/`) | HTTP: parse, call, choose a status code | SQL, business rules |
| **Service** (`services/`) | business rules and orchestration | `Request`, `HTTPException`, status codes |
| **Repository** (`repositories/`) | data access | HTTP, business rules |
| **Schema** (`schemas/`) | the wire contract | storage, HTTP |
| **Core** (`core/`) | config, errors, logging | everything above |

The direction of dependency is one-way: routers → services → repositories.
Nothing below ever imports upward.

```python
# api/v1/books.py — HTTP only
@router.post("", status_code=201, response_model=BookPublic)
async def create_book(payload: BookCreate, response: Response, request: Request):
    book = catalogue.add_book(payload)                  # ← business call
    response.headers["Location"] = str(request.url_for("get_book", book_id=book.id))
    return book

# services/catalogue.py — rules only, no HTTP vocabulary
def add_book(payload: BookCreate) -> Book:
    if repo.exists_isbn(payload.isbn):
        raise Conflict("duplicate_isbn", "A book with this ISBN already exists.")
    return repo.create(payload)
```

The service raises `Conflict` — an `APIError` from Day 06, not an
`HTTPException`. That keeps it callable from a CLI command, a background worker
(Day 18) or a test, none of which have a request. The HTTP mapping happens once,
in the error handler.

> **Do not add a service layer for pass-through code.** A `get_book` service that
> only calls `repo.get(id)` is noise. Add the layer when there is a *rule* — and
> not before.

## 7. `main.py` becomes assembly

```python
def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings)

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        lifespan=lifespan,
        docs_url=None if settings.is_production else "/docs",
        openapi_url=None if settings.is_production else "/openapi.json",
    )

    install_error_handlers(app)
    app.include_router(api_router, prefix=settings.api_prefix)
    app.include_router(health.router)
    return app
```

If you can read `main.py` in twenty seconds and know what the application is,
the structure is working. When it grows past a screen again, something in it
belongs in `core/`.

**Exception handlers are app-level.** `@router.exception_handler` does not exist —
registering handlers is `install_error_handlers(app)`, once. (Middleware, Day 13,
is also app-level; router-scoped behaviour is what dependencies are for.)

## 8. Circular imports, and the two ways out

The classic failure: `main` imports `books`, `books` imports `deps`, `deps`
imports `main` for `get_settings`. Python raises `ImportError: cannot import name
… (most likely due to a circular import)`.

Two fixes, in order of preference:

1. **Point the dependency downward.** `get_settings` belongs in `core/config.py`,
   which imports nothing from the app. Most cycles are a layering mistake wearing
   a disguise.
2. **Import inside the function** as a last resort — it defers the import to call
   time and breaks the cycle, at the cost of hiding the dependency.

Type-only cycles have a clean answer:

```python
from typing import TYPE_CHECKING
if TYPE_CHECKING:                       # imported by type checkers, not at runtime
    from shelfspace.services.catalogue import Catalogue

def handler(svc: "Catalogue") -> None: ...
```

## 9. Settings per environment

```python
class Settings(BaseSettings):
    environment: Literal["development", "test", "staging", "production"] = "development"
    api_prefix: str = "/api/v1"
    database_url: str = "sqlite:///./shelfspace.db"
    log_level: str = "INFO"
    cors_origins: list[str] = []

    @model_validator(mode="after")
    def production_is_strict(self):
        if self.environment == "production":
            if self.debug:
                raise ValueError("debug must be off in production")
            if "*" in self.cors_origins:
                raise ValueError("wildcard CORS is not allowed in production")
        return self
```

Encoding the rules **in the settings class** means a misconfigured production
deploy fails to start instead of running with debug on. That check is worth more
than any amount of documentation.

Keep one `Settings` class with environment-driven values, not three subclasses.
Subclasses drift: a field added to `DevSettings` and forgotten in `ProdSettings`
fails only in production, which is exactly where you did not want to find it.

## 10. Versioning: what gets a version, and what does not

```
/api/v1/books          ← business API: versioned from commit one
/api/v1/authors
/health                ← operational: never versioned
/metrics               ← operational (Day 21)
```

Once a third party depends on a URL, you cannot make a breaking change to it —
`/v1` is where you put the old behaviour while `/v2` exists. Adding a field is
not breaking; removing one, renaming one, or tightening validation is.

When `v2` arrives, the cheap structure is `api/v2/books.py` importing the v1
service layer and differing only in schemas. If your v2 has to duplicate business
logic, the logic was in the router.

Operational endpoints are for your infrastructure, not your customers. Versioning
them means changing your Kubernetes manifests to deploy an API change.

## 11. Where tests and `__init__.py` fit

Keep the test tree parallel to the source tree — `tests/api/v1/test_books.py`
next to `shelfspace/api/v1/books.py`. When a test fails you know which module to
open, and reviewers notice a missing test file.

`__init__.py` files should re-export sparingly:

```python
# api/__init__.py  ✅ one composed thing
from .v1 import books, authors
api_router = APIRouter()
api_router.include_router(books.router)
```

An `__init__.py` that imports everything from everywhere is how a cycle gets
built, and it makes import time slow and startup errors confusing.

## 12. Structure proportional to size

| Size | Structure |
|---|---|
| < 10 endpoints, one dev | a single `main.py` is genuinely fine |
| 10–50 endpoints | routers by resource + schemas + repositories |
| 50+ endpoints, a team | add services where rules exist; consider feature folders |
| Multiple teams | separate deployables before you separate layers further |

**Feature folders** (`books/{router,service,repo,schemas}.py`) are the other valid
layout, and are better when features are owned by different people — everything
you touch for one feature is in one directory. Layer folders are better when the
whole team touches everything. Choose one and be consistent; the failure mode is
half of each.

## 13. Best practices introduced today

| Practice | Reason |
|---|---|
| One `APIRouter` per resource | changes land in one file; merges stop conflicting |
| Set the version prefix in one place | `v2` becomes an inclusion change |
| `@router.get("")` for the collection root | avoids the trailing-slash redirect |
| Name routes used by `url_for` | function names collide across routers |
| Routers do HTTP; services do rules | the same rule then works in a worker or a CLI |
| Services raise `APIError`, never `HTTPException` | they must be callable without a request |
| One-way dependencies: router → service → repo | the only reliable defence against cycles |
| No service layer for pass-through calls | ceremony is not architecture |
| `main.py` is assembly only | readable in twenty seconds |
| Error handlers installed once, app-level | routers cannot register them |
| Fix cycles by fixing layering | function-level imports hide the problem |
| `TYPE_CHECKING` for type-only imports | no runtime cost, no cycle |
| One `Settings` class, env-driven | subclasses drift and fail in production |
| Validate production settings at startup | a bad deploy should refuse to start |
| Never version `/health` or `/metrics` | infrastructure should not track API versions |
| Test tree mirrors source tree | missing tests become visible |

## 14. Common mistakes

| Symptom | Cause | Fix |
|---|---|---|
| `ImportError: circular import` | upward import from a lower layer | move the shared piece into `core/` |
| Routes 404 after refactoring | router never included | `app.include_router(...)` |
| Paths doubled: `/books/books` | prefix set on both router and include | set it once |
| `/api/v1/books/` redirects | `@router.get("/")` under a prefix | use `""` |
| `url_for` builds the wrong URL | duplicate route names | pass `name=` explicitly |
| Error handlers stopped working | moved to a router | they are app-level |
| Business logic duplicated in v2 | logic lived in the router | move it to a service |
| Ten files to add one field | premature layering | collapse pass-through layers |
| Prod ran with debug on | no startup validation | validate in the settings model |
| A setting works in dev, missing in prod | settings subclasses | one class |
| Nobody can find an endpoint | flat `main.py` | routers by resource |
| Merge conflicts in one file | everything in `main.py` | routers by resource |
| Slow startup, confusing import errors | `__init__.py` imports everything | re-export sparingly |
| Half feature-folders, half layers | two layouts at once | pick one |
| `/health` versioned | copied the business prefix | keep it unversioned |
| Tests import the app and fail on config | module-level `app` | `create_app(settings)` (Day 01) |

## 15. Exercises

1. Split Day 06's app into the tree in section 2 without changing a single
   response. Verify with `diff` on the `curl` outputs before and after.
2. Print your routing table with the `openapi.json` snippet in section 4. Look
   for accidental duplicates and trailing-slash variants.
3. Add `/api/v2/books` that returns `publishedYear` in camelCase while `v1` keeps
   snake_case — reusing the same service. If you cannot, your logic is in the
   wrong layer.
4. Deliberately create a circular import, read the error, then fix it by moving
   the shared piece down rather than by importing inside a function.
5. Add `cors_origins` and `log_level` settings and make production reject a `*`
   origin at startup. Confirm it refuses to boot.
6. Move `/health` into its own unversioned router and write down why your load
   balancer config is happier.
7. Restructure one resource as a feature folder (`books/`) and compare it with
   the layer layout. Pick one and note the reason.
8. Add `tests/api/v1/test_books.py` mirroring the source path, and run
   `pytest tests/api` alone.

## 16. What's next

**[Day 08 — Dependency Injection →](../08_dependency_injection/)**
`deps.py` is still empty. Tomorrow it holds the machinery that makes this
structure work: shared pagination, per-request resources with `yield`, cached
sub-dependencies, and the override that lets every test swap the database for a
fake in one line.
