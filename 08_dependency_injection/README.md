# Day 08 — Dependency Injection

> **Goal:** learn FastAPI's best idea — declare what an endpoint needs, let the
> framework supply it, and swap any of it for a fake in one line during tests.
> **Time:** ~2.5 hours · **Port:** 8008 · **Builds on:** Day 07

> **Code status:** the README is the spec. Build `shelfspace/` yourself from
> sections 5 onwards before reaching for a reference implementation.

---

## 1. Why this matters

> **`Depends` is not a design pattern imported from Java. It is how FastAPI
> makes shared behaviour reusable, testable, and visible in the signature.**

Without it, every endpoint that needs pagination re-declares three parameters and
re-clamps the limit; every endpoint that needs the current user re-parses the
header; every test that needs a fake database monkeypatches a module global and
prays nothing else imported it first.

With it, all three are one parameter — and the parameter is also documentation,
validation, and a test seam.

## 2. What you will build

```
08_dependency_injection/
├── run.py
└── shelfspace/
    ├── api/
    │   ├── deps.py          the lesson lives here
    │   └── v1/books.py
    ├── core/config.py
    ├── repositories/books.py
    └── services/catalogue.py
```

Dependencies you will write:

| Dependency | Provides | Style |
|---|---|---|
| `get_settings` | validated configuration | cached, `lru_cache` |
| `Pagination` | `limit` / `offset`, clamped | class-based |
| `get_repo` | a repository bound to a connection | `yield` (setup + teardown) |
| `get_current_user` | the caller, from a header | sub-dependency (Day 15 makes it real) |
| `require_api_key` | nothing — it just guards | route-level, no return value |
| `SortSpec` | a validated sort field + direction | reusable `Annotated` type |

## 3. Run it

```bash
source .venv/bin/activate
cd 08_dependency_injection
python run.py
```

## 4. Try it — learn by doing

```bash
API=http://127.0.0.1:8008/api/v1

# --- one dependency, identical pagination on every collection ---
curl -s "$API/books?limit=2"              | python -m json.tool
curl -s "$API/authors?limit=2"            | python -m json.tool
curl -s "$API/books?limit=999999"         | python -m json.tool   # clamped once, centrally

# --- a guard dependency that returns nothing but can reject ---
curl -s  $API/admin/stats                          | python -m json.tool   # 401
curl -s  $API/admin/stats -H "X-API-Key: dev-key"  | python -m json.tool   # 200

# --- sub-dependencies: user ← token ← header ---
curl -s  $API/me                                   | python -m json.tool   # 401
curl -s  $API/me -H "Authorization: Bearer alice"  | python -m json.tool

# --- caching: the SAME dependency used three times runs ONCE per request ---
curl -s $API/books/1/report | python -m json.tool
# watch the server log: "loading settings" appears once, not three times

# --- opt out of caching and it runs every time ---
curl -s $API/debug/uncached | python -m json.tool   # three different timestamps

# --- yield dependencies: teardown runs even when the handler raises ---
curl -s $API/books/9999   | python -m json.tool     # 404
# server log: "repo closed" — the teardown still ran

# --- dependencies appear in the OpenAPI schema ---
curl -s http://127.0.0.1:8008/openapi.json | python -c "
import json,sys
p = json.load(sys.stdin)['paths']['/api/v1/books']['get']
print([q['name'] for q in p.get('parameters', [])])"
```

The caching line is the one people miss, and it is the reason a `get_db`
dependency used by four sub-dependencies still opens **one** connection.

## 5. `Depends` in one page

```python
from typing import Annotated
from fastapi import Depends

def pagination(limit: int = 20, offset: int = 0) -> dict:
    return {"limit": min(limit, 100), "offset": offset}

@router.get("/books")
async def list_books(page: Annotated[dict, Depends(pagination)]):
    ...
```

FastAPI reads the signature, sees `Depends`, calls `pagination` first, and
passes the result in. Crucially, `pagination`'s own parameters (`limit`,
`offset`) become **query parameters of every endpoint that depends on it**,
complete with validation and `/docs` entries. A dependency is a piece of
signature you can reuse.

Give it an alias so the noise appears once:

```python
# deps.py
PaginationDep = Annotated[Pagination, Depends(pagination)]

# books.py
async def list_books(page: PaginationDep): ...
```

Anything callable works: a function, an `async def`, a class, or an instance with
`__call__`. A `def` dependency runs in the thread pool exactly like a `def`
endpoint (Day 14), so a blocking dependency does not block the event loop.

## 6. Class-based dependencies

```python
class Pagination:
    def __init__(
        self,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        offset: Annotated[int, Query(ge=0)] = 0,
    ):
        self.limit = limit
        self.offset = offset

    def slice(self, items: list) -> list:
        return items[self.offset : self.offset + self.limit]

PaginationDep = Annotated[Pagination, Depends(Pagination)]
```

You get a typed object with behaviour attached, and `page.limit` autocompletes —
unlike a dict, where a typo is a `KeyError` at runtime.

For a dependency that needs its own configuration, use a **callable instance**:

```python
class RequireRole:
    def __init__(self, role: str):
        self.role = role
    def __call__(self, user: UserDep) -> User:
        if self.role not in user.roles:
            raise APIError(403, "forbidden", f"Requires the {self.role} role.")
        return user

require_admin = RequireRole("admin")        # Day 16 builds this out
```

## 7. Sub-dependencies

Dependencies can depend on dependencies, to any depth:

```python
def get_token(authorization: Annotated[str | None, Header()] = None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise APIError(401, "unauthenticated", "Bearer token required.")
    return authorization.removeprefix("Bearer ")

def get_current_user(token: Annotated[str, Depends(get_token)],
                     repo: RepoDep) -> User:
    user = repo.find_by_token(token)
    if user is None:
        raise APIError(401, "invalid_token", "Token is not valid.")
    return user

UserDep = Annotated[User, Depends(get_current_user)]
```

The endpoint asks for `user: UserDep` and the whole chain — header → token →
lookup — runs before the handler. FastAPI resolves the graph, so the handler's
signature stays one line no matter how deep it goes.

## 8. Caching: once per request, by default

> **Within a single request, a dependency is called once per unique
> `Depends(...)` object, and its result is reused everywhere it appears.**

```python
async def report(a: SettingsDep, b: SettingsDep, c: SettingsDep):
    # get_settings ran ONCE
```

This is what makes deep graphs cheap: `get_db` appearing in five
sub-dependencies opens one connection, not five.

To opt out — a per-call timestamp, a fresh random value:

```python
Annotated[float, Depends(now, use_cache=False)]
```

Two caveats worth knowing:

- The cache is **per request**, not global. Use `@lru_cache` for process-wide
  values like settings.
- The cache key is the dependency *callable*, so `Depends(RequireRole("admin"))`
  and `Depends(RequireRole("editor"))` are correctly distinct objects — but two
  identically-configured instances created inline are also distinct, and each
  runs. Create them once at module level.

## 9. `yield` dependencies: setup, teardown, and where errors land

```python
def get_repo() -> Iterator[BookRepository]:
    conn = open_connection()
    try:
        yield BookRepository(conn)          # ← the handler runs here
    finally:
        conn.close()                        # ← always runs

RepoDep = Annotated[BookRepository, Depends(get_repo)]
```

This is how every database session works from Day 09. Three rules:

**Always use `try/finally`.** Without it, an exception in the handler skips your
cleanup and leaks a connection per failed request — which is how a pool is
exhausted in production but never in testing.

**Teardown runs *after* the response is generated.** So raising an exception in
the code after `yield` cannot change the response the client already has. Do
cleanup there, not logic.

**Handle failure explicitly if you need to.** For a transaction, commit on
success and roll back on error:

```python
def get_session():
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
```

Day 10 debates whether that commit belongs here or in the service. For now, note
that both branches must exist.

## 10. Dependencies that guard rather than provide

```python
def require_api_key(x_api_key: Annotated[str | None, Header()] = None) -> None:
    if x_api_key != settings.api_key:
        raise APIError(401, "invalid_api_key", "A valid X-API-Key is required.")

# route level
@router.get("/stats", dependencies=[Depends(require_api_key)])

# router level — every route inside
router = APIRouter(prefix="/admin", dependencies=[Depends(require_api_key)])

# app level — everything
app = FastAPI(dependencies=[Depends(require_api_key)])
```

When a dependency returns nothing useful, put it in `dependencies=[...]` rather
than the signature: the handler stays free of a parameter it never reads, and the
guard is still visible and still documented.

**Apply auth at the router or app level, not per endpoint.** A per-endpoint
decorator is one `git merge` away from a new public endpoint that nobody
protected. Router-level defaults fail safe — a new route is guarded unless
someone deliberately opts it out.

## 11. Overrides: the reason tests are easy

```python
# conftest.py
def fake_repo():
    return InMemoryBookRepository([...])

app.dependency_overrides[get_repo] = fake_repo

client = TestClient(app)
# ...
app.dependency_overrides.clear()          # always clean up
```

No monkeypatching, no import-order games, no live database. The override is keyed
on the **function object**, so it replaces that dependency wherever it appears in
the graph — including three levels down.

This only works if your code takes the dependency as a parameter. The moment a
service reaches for a module-level global instead, the seam disappears:

```python
db = Database()                       # ❌ untestable, unoverridable
def get_db(): ...                     # ✅ a seam
```

That is the real argument for dependency injection — not purity, but the ability
to replace one piece without touching the rest.

## 12. When *not* to use a dependency

Dependencies are cheap, not free. Skip them when:

- **The value is a constant.** Import it.
- **It is used once.** A dependency used by a single endpoint is indirection
  without reuse; inline it until a second caller appears.
- **You need it outside a request.** A background worker or CLI command has no
  request scope. Write a plain function and have the dependency wrap it —
  `get_repo()` calls `build_repo()`, and the worker calls `build_repo()` too.
- **It does heavy work on every request.** A dependency that loads a model or
  opens a connection per request belongs in `lifespan`, stored on `app.state`,
  with a trivial dependency that hands it out.

```python
# lifespan
app.state.http = httpx.AsyncClient()

def get_http(request: Request) -> httpx.AsyncClient:
    return request.app.state.http      # cheap; the client is shared
```

## 13. Best practices introduced today

| Practice | Reason |
|---|---|
| Take dependencies as parameters, never module globals | globals cannot be overridden in tests |
| Alias with `Annotated[X, Depends(f)]` in `deps.py` | one import, no repeated noise |
| One `Pagination` dependency for every collection | the limit is clamped in one place |
| Class-based dependencies for typed, behavioural values | `page.limit` beats `page["limit"]` |
| Callable instances for configurable guards | `RequireRole("admin")` |
| Create configured dependency instances at module level | inline instances defeat caching |
| `try/finally` in every `yield` dependency | otherwise failures leak resources |
| Put guards in `dependencies=[...]` | keeps unused parameters out of handlers |
| Apply auth at router/app level | a new route is protected by default |
| `use_cache=False` only when you mean it | caching is what keeps graphs cheap |
| `@lru_cache` for process-wide values | request cache is per request |
| Heavy resources in `lifespan`, handed out by a trivial dependency | one client, not one per request |
| Dependencies wrap plain functions | workers and CLIs have no request scope |
| `dependency_overrides` in tests, cleared afterwards | leaks between tests are brutal to debug |

## 14. Common mistakes

| Symptom | Cause | Fix |
|---|---|---|
| `Depends()` object appears in the response | forgot to annotate, returned it | annotate with `Annotated[T, Depends(f)]` |
| Dependency never runs | wrote `Depends(f())` instead of `Depends(f)` | pass the callable, not the call |
| Connections exhausted under load | no `finally` in a `yield` dependency | `try/finally` |
| Cleanup skipped on errors | same | same |
| Five connections per request | expected caching across different callables | one shared dependency |
| Timestamp identical across a request | dependency caching | `use_cache=False` |
| Two guards with different config behave the same | inline instances, per-request cache confusion | module-level instances |
| Override does nothing | overrode the alias, not the function | key on the original callable |
| Tests contaminate each other | overrides never cleared | `clear()` in teardown |
| Auth missing on a new endpoint | per-endpoint guards | router-level `dependencies` |
| Handler has an unused `_: None = Depends(guard)` | guard in the signature | move it to `dependencies=[...]` |
| Event loop stalls | blocking call inside an `async def` dependency | make it `def`, or await properly (Day 14) |
| Worker cannot reuse endpoint logic | logic lives in the dependency | dependency wraps a plain function |
| Model reloaded on every request | heavy work in a dependency | `lifespan` + `app.state` |
| `Header()` parameter never populated | name mismatch | `x_api_key` maps to `X-API-Key` automatically |
| Circular import in `deps.py` | dependency imports a router | dependencies live below routers |

## 15. Exercises

1. Write `Pagination` as a class dependency and use it on `/books` and
   `/authors`. Change the cap to 50 in one place and confirm both endpoints
   change.
2. Build the `get_token → get_current_user` chain and add `/me`. Confirm a
   missing header gives your Day 06 `401` envelope, not FastAPI's default.
3. Add a `get_repo` `yield` dependency that logs "opened"/"closed", then request
   an endpoint that raises a 404. Confirm "closed" still appears.
4. Prove caching: log inside `get_settings`, depend on it three times in one
   endpoint, count the log lines. Then set `use_cache=False` and count again.
5. Move `require_api_key` from a single route to a router-level dependency, add a
   new admin route without touching auth, and confirm it is already protected.
6. Write a test that overrides `get_repo` with an in-memory fake and asserts on
   `/books` with no database at all.
7. Build `RequireRole("admin")` as a callable instance, create two instances, and
   check both run for a route that uses both.
8. Move an `httpx.AsyncClient` into `lifespan` + `app.state` with a trivial
   dependency, and measure the difference against creating one per request.

## 16. What's next

**[Day 09 — Databases with SQLAlchemy →](../09_databases_with_sqlalchemy/)**
Every list so far has been in memory and vanishes on restart. Tomorrow the data
becomes real: SQLAlchemy 2.0 models, a session per request delivered by exactly
the `yield` dependency you just wrote, and Alembic migrations.
