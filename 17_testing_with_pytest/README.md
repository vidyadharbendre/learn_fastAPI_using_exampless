# Day 17 — Testing with Pytest

> **Goal:** a test suite you actually run — isolated per test, fast enough for
> every save, and structured so a failure tells you what broke rather than that
> something did.
> **Time:** ~3 hours · **Port:** 8017 · **Builds on:** Day 16

> **Code status:** the README is the spec. Build `tests/` yourself from sections
> 5 onwards. (Day 01's `tests/` in this repo is a working miniature of it.)

---

## 1. Why this matters

> **A slow, flaky test suite is worse than no test suite: it costs the same time
> and teaches the team to ignore red.**

The failure mode is predictable. Tests share a database, so they must run in
order. One leaves a row behind, so another fails intermittently. Someone adds
`time.sleep(0.5)` to "fix" it. The suite takes four minutes, so people push
without running it, so CI is the first place failures appear, so failures are
found at the least convenient moment.

Everything in this day exists to prevent that specific spiral.

## 2. What you will build

```
17_testing_with_pytest/
├── pytest.ini
└── tests/
    ├── conftest.py           engine, session, client, auth fixtures
    ├── factories.py          build objects without repeating yourself
    ├── unit/
    │   ├── test_policy.py    permission rules — no DB, microseconds
    │   └── test_schemas.py   validators
    ├── integration/
    │   ├── test_repositories.py
    │   └── test_services.py  business rules against a real database
    └── api/
        ├── test_books.py     status codes, envelopes, contracts
        ├── test_auth.py
        └── test_permissions.py   the matrix from Day 16
```

## 3. Run it

```bash
source .venv/bin/activate
cd 17_testing_with_pytest

pytest                      # everything
pytest -x -q                # stop at the first failure
pytest tests/unit           # the fast half
pytest -k "permission"      # by name
pytest -m "not slow"        # by marker
pytest --cov=shelfspace --cov-report=term-missing
pytest -n auto              # parallel (pytest-xdist)
pytest --lf                 # only what failed last time
```

## 4. Try it — learn by doing

```bash
# --- the suite should be fast. Measure it. ---
pytest -q --durations=10

# --- isolation: run it twice; identical results, no leftovers ---
pytest -q && pytest -q

# --- and in a random order, which is where shared state gets exposed ---
pip install pytest-randomly && pytest -q          # order changes each run

# --- prove a test cannot see another's data ---
pytest tests/integration/test_isolation.py -v

# --- coverage that points at what is missing, not a number ---
pytest --cov=shelfspace --cov-report=term-missing | tail -25

# --- find the slow tests, not the failing ones ---
pytest --durations=0 | grep -E 'call\s+tests' | head

# --- a deliberately flaky test, run 20 times ---
pytest tests/api/test_books.py::test_list_order -q --count=20    # pytest-repeat

# --- parallel: the real test of isolation ---
pytest -n 4 -q

# --- what a good failure looks like ---
pytest tests/api/test_books.py::test_create_returns_201 -vv
```

If `pytest -n 4` fails but `pytest` passes, your tests share state. That is the
diagnostic, and it is worth running before you believe any of this works.

## 5. The pyramid, in practice

| Layer | Tests | Speed | What it proves |
|---|---|---|---|
| **Unit** | pure functions: policies, validators, token encoding | µs | the rule is right |
| **Integration** | services + repositories against a real database | ms | the wiring and SQL are right |
| **API** | endpoints through `TestClient` | ms | the contract is right |
| **E2E** | the deployed system | s–min | it is actually plugged in |

Aim for many unit tests, a solid layer of integration tests, and **one API test
per endpoint per outcome** — not one per branch of business logic. Testing every
permission combination through HTTP is slow and tests the same rule repeatedly;
test the rule in `test_policy.py` and the wiring once in `test_permissions.py`.

The layering from Day 07 and Day 10 is what makes this possible. A rule inside a
handler can only be tested through HTTP.

## 6. Database isolation: transaction rollback per test

The fastest correct approach — a real database, no truncation, nothing shared:

```python
@pytest.fixture(scope="session")
def engine():
    engine = create_engine("sqlite:///./test.db")   # or a test PostgreSQL
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()

@pytest.fixture
def session(engine):
    """Each test runs inside a transaction that is rolled back afterwards."""
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()          # ← everything the test did, undone
        connection.close()
```

Why this and not the alternatives:

| Approach | Speed | Notes |
|---|---|---|
| **Transaction rollback** | fastest | nothing to clean up; `commit()` inside is contained by the savepoint |
| Truncate tables between tests | slower | fine, and simpler to explain |
| Recreate the schema per test | slowest | only for schema-level tests |
| Shared database, ordered tests | fast until it is not | this is the spiral in section 1 |

**Test against the database you deploy on.** SQLite is convenient and differs
from PostgreSQL in ways that matter: no real `ALTER`, different constraint
behaviour, no `ILIKE`, looser typing, no concurrent writers. A green SQLite suite
and a broken production is a bad afternoon; run PostgreSQL in CI at minimum
(Docker makes this cheap).

## 7. The client fixture, with overrides

```python
@pytest.fixture
def client(session) -> Iterator[TestClient]:
    app = create_app(Settings(environment="test"))

    app.dependency_overrides[get_session] = lambda: session   # the SAME session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()                          # always
```

Two things this buys (Day 08):

- The endpoint and the test share **one** session, so data the test creates is
  visible to the request, and the rollback undoes both.
- `create_app(Settings(environment="test"))` (Day 01) means test configuration
  never leaks from your shell.

For authenticated requests, build the token rather than logging in over HTTP each
time — bcrypt in every test is why suites get slow:

```python
@pytest.fixture
def auth_client(client, user_factory):
    def _as(role=Role.member):
        user = user_factory(role=role)
        token = create_access_token(user)
        client.headers["Authorization"] = f"Bearer {token}"
        return client, user
    return _as
```

Faster still for permission tests: override `get_current_user` directly, so no
token is created or parsed at all. Keep one real end-to-end login test so the
actual auth path is still covered.

## 8. Factories over fixtures for data

```python
# factories.py
def make_book(session, **overrides) -> Book:
    defaults = dict(isbn=unique_isbn(), title="Test Book",
                    price=Decimal("100.00"), stock=5, author_id=None)
    book = Book(**(defaults | overrides))
    session.add(book)
    session.flush()
    return book
```

```python
def test_out_of_stock_cannot_be_borrowed(session):
    book = make_book(session, stock=0)     # the ZERO is the point of the test
```

Every test states only what matters to it. Compare with a shared
`sample_book` fixture: the moment two tests need different stock levels you get
`sample_book`, `sample_book_no_stock`, `sample_book_expensive`, and a reader has
to go and look up what each contains.

Two rules: **unique values by default** (an incrementing ISBN, not a constant, or
you will fight unique constraints), and **no cross-test sharing** — a factory
creates fresh objects; it does not memoise.

## 9. Testing async code

```ini
# pytest.ini
[pytest]
asyncio_mode = auto          # no @pytest.mark.asyncio on every test
```

`TestClient` is synchronous and drives the async app for you — that is enough for
most API tests. Use a real async client when you are testing async code paths
directly, or need concurrency:

```python
@pytest.fixture
async def async_client(app) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

async def test_concurrent_borrow_only_one_wins(async_client):
    results = await asyncio.gather(*(
        async_client.post("/api/v1/books/1/borrow") for _ in range(5)))
    assert sum(r.status_code == 201 for r in results) == 1
```

That last test is the kind that finds real bugs — the race conditions Day 09 and
Day 12 warned about. Note it needs a database that supports concurrent writers,
which SQLite does not.

## 10. What to assert

Test **behaviour**, not implementation:

```python
# ❌ breaks on every refactor, proves nothing about the API
assert repo.create.call_count == 1

# ✅ describes the contract
assert response.status_code == 201
assert response.headers["Location"].endswith(f"/books/{body['id']}")
assert body["price"] == "4199.00"
```

Assert on the things you promised in Days 05, 06 and 12: status code, `Location`,
the response shape, the error envelope's `code`, pagination `meta`. Those are
your contract; everything else is free to change.

Include the failures. A suite that only tests success paths misses most of what
this course has been about:

```python
@pytest.mark.parametrize("payload,code", [
    ({"title": ""},                     "validation_error"),
    ({"isbn": "duplicate"},             "duplicate_isbn"),
    ({"price": "-1"},                   "validation_error"),
])
def test_create_book_rejects(client, payload, code):
    r = client.post("/api/v1/books", json=payload)
    assert r.json()["error"]["code"] == code
```

And keep the structural tests from earlier days — query counts (Day 11), "every
route is protected" (Day 16), the error envelope shape (Day 06). Those catch
whole categories of regression that per-endpoint tests never will.

## 11. Mocking: as little as possible

```python
# ✅ mock what you do not control: third-party HTTP, email, payment providers
respx.post("https://payments.example/charge").mock(
    return_value=httpx.Response(200, json={"id": "ch_1", "status": "ok"}))

# ❌ do not mock your own database, repositories or services
```

Mocking your own code tests that your mocks agree with your mocks. When the real
repository changes signature, the mocked tests still pass — which is precisely
backwards.

For third-party HTTP, `respx` (for httpx) beats hand-rolled patching, and record
one **contract test** against the real service in a separate, slow-marked suite so
you learn when their API changes.

For time, do not sleep — inject a clock or freeze it:

```python
@pytest.fixture
def frozen_time(monkeypatch):
    monkeypatch.setattr("shelfspace.utils.utcnow", lambda: datetime(2026, 1, 1, tzinfo=UTC))
```

`time.sleep` in tests is the most common cause of a four-minute suite.

## 12. Coverage, honestly

```bash
pytest --cov=shelfspace --cov-report=term-missing --cov-fail-under=80
```

Coverage tells you what is **not** tested. It says nothing about whether what *is*
covered is tested well — a test that calls a function and asserts nothing shows
100%.

Read `term-missing` and ask which uncovered lines are error paths, permission
checks, or edge cases; those are the ones worth adding. Chasing the last 10% of
coverage across trivial getters is time better spent on the `except` branch nobody
has ever executed.

A number to enforce in CI is still useful — it stops coverage silently rotting —
but treat 80% as a floor, not a goal.

## 13. Best practices introduced today

| Practice | Reason |
|---|---|
| Every test isolated by a rolled-back transaction | order-independent, parallel-safe |
| Run tests in random order and in parallel | it is how shared state is exposed |
| Test against the database you deploy on | SQLite and PostgreSQL differ where it matters |
| One session shared by test and app, via override | the request sees the test's data |
| Clear `dependency_overrides` after every test | leaks between tests are brutal to debug |
| Factories with unique defaults, not shared fixtures | each test states only what matters |
| Test rules at the service layer, wiring at the API | fast suite, real coverage |
| One API test per endpoint per outcome | not one per business branch |
| Assert on the contract, not on call counts | refactors should not break tests |
| Parametrise the failure cases | most of your behaviour is failure behaviour |
| Keep structural tests (query count, auth, envelope) | they catch whole categories |
| Mock only what you do not control | mocking your own code proves nothing |
| Freeze time; never `sleep` | sleeps are why suites are slow |
| Build tokens directly, or override the user dependency | bcrypt per test is the other reason |
| `--cov-report=term-missing`, floor not target | coverage shows gaps, not quality |
| Name tests as sentences | the output becomes a specification |

## 14. Common mistakes

| Symptom | Cause | Fix |
|---|---|---|
| Tests pass alone, fail together | shared database state | rollback fixture |
| Tests pass in order, fail randomised | hidden inter-test dependency | fix the dependency, not the order |
| `pytest -n 4` fails, `pytest` passes | shared state or a shared file/port | isolate per test |
| Data created in a test is invisible to the app | different sessions | override `get_session` |
| Overrides leak into the next test | never cleared | `clear()` in the fixture teardown |
| Suite takes minutes | bcrypt, sleeps, everything via HTTP | direct tokens, frozen time, unit tests |
| Flaky assertion on list order | no `ORDER BY` | Day 10 — and fix the app, not the test |
| Green suite, broken production | SQLite in tests, PostgreSQL in prod | test on the real engine |
| Tests break on every refactor | asserting implementation details | assert behaviour |
| 100% coverage, obvious bugs | assertions missing | read the tests, not the number |
| Only happy paths covered | failures never tested | parametrise errors |
| Mocked repository hid a real break | mocking your own code | use the real one |
| Async test never runs | `asyncio_mode` unset | `asyncio_mode = auto` |
| Race-condition test always passes | SQLite has one writer | use PostgreSQL for it |
| CI passes, local fails | environment leaking from your shell | `create_app(Settings(environment="test"))` |
| Nobody runs the suite | it is slow and flaky | fix speed first; trust follows |

## 15. Exercises

1. Build the `engine`/`session`/`client` fixtures with transaction rollback, then
   run `pytest` twice and confirm the database is unchanged.
2. Install `pytest-randomly` and `pytest-xdist`, run `pytest -n 4 -p randomly`,
   and fix everything that breaks. Do not "fix" it by pinning the order.
3. Write factories for `Book`, `User` and `Order` with unique defaults, then
   rewrite three existing tests to use them and compare readability.
4. Convert five permission tests from HTTP to direct policy tests and measure the
   time saved.
5. Write the concurrency test in section 9 against PostgreSQL and make it pass —
   you will need Day 12's optimistic locking or a database-level constraint.
6. Add the query-count assertion from Day 11 and the "every route is protected"
   test from Day 16 to this suite.
7. Mock a third-party payment call with `respx`, then write a slow-marked
   contract test against a sandbox endpoint and mark it `-m slow`.
8. Run `--cov-report=term-missing`, pick the three most dangerous uncovered
   lines, and write tests for exactly those.

## 16. What's next

**[Day 18 — Background Tasks and Workers →](../18_background_tasks_and_workers/)**
Some work should not happen while a client waits: emails, reports, image
processing. Tomorrow: `BackgroundTasks`, when it is genuinely enough, when you
need a real queue, and the retry and idempotency rules that keep "eventually" from
meaning "sometimes never".
