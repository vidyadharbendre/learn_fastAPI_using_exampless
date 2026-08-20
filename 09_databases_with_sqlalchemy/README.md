# Day 09 — Databases with SQLAlchemy

> **Goal:** make the data real — SQLAlchemy 2.0 models, a session per request,
> transactions that actually commit or actually roll back, and Alembic migrations
> so the schema can change after you have users.
> **Time:** ~3 hours · **Port:** 8009 · **Builds on:** Day 08

> **Code status:** the README is the spec. Build `shelfspace/` yourself from
> sections 5 onwards before reaching for a reference implementation.

---

## 1. Why this matters

> **Everything you have built so far forgets itself when you press Ctrl-C.**

Adding a database is not "swap the list for a query". It brings four new
concerns, and skipping any of them causes a specific, well-known outage:

| Concern | What goes wrong if you skip it |
|---|---|
| **Session lifetime** | connections leak until the pool is exhausted |
| **Transactions** | half-written data, and nobody notices for weeks |
| **Migrations** | `create_all()` in production and a schema nobody can reproduce |
| **Constraints** | duplicate rows that your application check "prevents" |

Today does all four. It is the longest day of the course and the one that changes
your app from a demo into a service.

## 2. What you will build

```
09_databases_with_sqlalchemy/
├── run.py
├── alembic.ini
├── migrations/
│   ├── env.py
│   └── versions/            each schema change, versioned and reversible
└── shelfspace/
    ├── core/config.py       database_url, pool settings
    ├── db/
    │   ├── base.py          DeclarativeBase + naming conventions
    │   ├── session.py       engine, SessionLocal, get_session dependency
    │   └── models.py        Book, Author — real tables
    ├── repositories/books.py  select() statements live here
    ├── services/catalogue.py  transaction boundaries live here
    └── api/v1/books.py        unchanged from Day 07 — that is the point
```

## 3. Run it

```bash
source .venv/bin/activate
cd 09_databases_with_sqlalchemy

alembic upgrade head          # create the schema — never create_all()
python -m shelfspace.seed     # a little data to look at
python run.py
```

```bash
sqlite3 shelfspace.db ".schema books"     # look at what Alembic actually built
```

## 4. Try it — learn by doing

```bash
API=http://127.0.0.1:8009/api/v1
JSON='Content-Type: application/json'

# --- data that survives a restart ---
curl -sX POST $API/books -H "$JSON" -d '{
  "isbn":"978-1-4919-4600-8","title":"Fluent Python",
  "price":"4199.00","stock":7,"author_id":1}' | python -m json.tool
# Ctrl-C the server, start it again:
curl -s $API/books | python -m json.tool | grep -c Fluent      # still there

# --- the database enforces uniqueness, not your `if` statement ---
curl -s -o /dev/null -w 'first  = %{http_code}\n' -X POST $API/books -H "$JSON" \
  -d '{"isbn":"978-0-13-235088-4","title":"Clean Code","price":"3500.00","stock":1,"author_id":1}'
curl -s -o /dev/null -w 'second = %{http_code}\n' -X POST $API/books -H "$JSON" \
  -d '{"isbn":"978-0-13-235088-4","title":"Clean Code","price":"3500.00","stock":1,"author_id":1}'
# 409, from a caught IntegrityError — not a 500

# --- a foreign key that does not exist ---
curl -s -X POST $API/books -H "$JSON" -d '{"isbn":"978-0-00-000000-2","title":"Orphan",
  "price":"100.00","stock":1,"author_id":9999}' | python -m json.tool

# --- transactions: all of it, or none of it ---
curl -sX POST $API/orders -H "$JSON" \
  -d '{"items":[{"book_id":1,"qty":2},{"book_id":9999,"qty":1}]}' | python -m json.tool
curl -s $API/books/1 | python -c "import json,sys; print('stock =', json.load(sys.stdin)['stock'])"
# unchanged: the first line rolled back with the second

# --- see the SQL your code actually produced ---
SHELFSPACE_SQL_ECHO=true python run.py       # then hit /books and read the log

# --- sessions are closed even when the handler raises ---
curl -s $API/books/9999 | python -m json.tool     # 404; log shows "session closed"
```

The transaction test is the one to internalise. Two writes, one bad, **zero**
partial state.

## 5. SQLAlchemy 2.0, not the tutorials you will find

The 2.0 style is genuinely different from the `Query`-based code that fills the
internet. Learn it directly:

```python
# db/base.py
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

class Base(DeclarativeBase):
    metadata = MetaData(naming_convention={
        "ix": "ix_%(column_0_label)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    })
```

The naming convention matters more than it looks: without it, SQLite and
PostgreSQL invent different constraint names, and an Alembic migration that
drops a constraint by name works on one and fails on the other.

```python
# db/models.py
class Book(Base):
    __tablename__ = "books"

    id: Mapped[int] = mapped_column(primary_key=True)
    isbn: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(200), index=True)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    stock: Mapped[int] = mapped_column(default=0)
    author_id: Mapped[int] = mapped_column(ForeignKey("authors.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    author: Mapped["Author"] = relationship(back_populates="books")

    __table_args__ = (
        CheckConstraint("stock >= 0", name="ck_books_stock_non_negative"),
        CheckConstraint("price > 0", name="ck_books_price_positive"),
    )
```

Four decisions in that class:

- **`Mapped[int]` vs `Mapped[int | None]`** determines nullability. The type
  annotation *is* the schema — no separate `nullable=` needed.
- **`Numeric(10, 2)` for money.** Never `Float`. A binary float cannot hold
  `0.10`, and the error compounds across a million rows.
- **`DateTime(timezone=True)`** is a request the backend may ignore. PostgreSQL
  honours it; **SQLite has no timezone type at all**, so normalise to UTC when
  you serialise (Day 01) rather than trusting the column.
- **`CheckConstraint`** is the last line of defence. Pydantic validates the
  request; the constraint survives a bug, a migration script, and someone with
  `psql`.

Queries use `select()`, and the session executes them:

```python
stmt = select(Book).where(Book.stock > 0).order_by(Book.title).limit(20)
books = session.scalars(stmt).all()

book = session.get(Book, book_id)                    # by primary key — cheap
total = session.scalar(select(func.count()).select_from(Book))
```

> `session.query(Book)` is 1.x legacy. It still runs and it is everywhere online.
> Write `select()`.

## 6. Engine and session: one engine, many sessions

```python
# db/session.py
engine = create_engine(
    settings.database_url,
    pool_size=5, max_overflow=10, pool_pre_ping=True, pool_recycle=1800,
    echo=settings.sql_echo,
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
```

| Setting | Why |
|---|---|
| `pool_size` | steady-state connections **per process** — multiply by your worker count |
| `max_overflow` | temporary extras under load |
| `pool_pre_ping` | tests a connection before use; without it, every idle-timeout gives one user a 500 |
| `pool_recycle` | drop connections before the database or a proxy kills them |
| `expire_on_commit=False` | you can read attributes after `commit()` (see below) |

**One engine per process, created once** (at import of `session.py`, or in
`lifespan`). An engine per request creates a connection pool per request, which
is the opposite of pooling.

**`expire_on_commit=False` deserves a note.** By default, SQLAlchemy expires every
object after a commit, so touching `book.title` afterwards fires a fresh
`SELECT` — and if the session is already closed you get
`DetachedInstanceError`, usually while FastAPI is serialising your response.
Turning it off is the pragmatic default for web apps.

## 7. A session per request — Day 08's `yield` dependency, for real

```python
def get_session() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()

SessionDep = Annotated[Session, Depends(get_session)]
```

**One session per request. Never a global session, never one per query.**

- A **global** session is shared mutable state across concurrent requests: one
  request's rollback discards another's work, and `Session` is not thread-safe.
- A **session per query** loses transactional atomicity — the whole point.

Because it is a dependency (Day 08), every layer below receives the same session,
and tests override it with one bound to a throwaway database in a single line.

## 8. Transactions: where `commit()` belongs

```python
# services/catalogue.py — the service owns the boundary
def create_order(session: Session, payload: OrderCreate) -> Order:
    order = Order(...)
    session.add(order)
    for item in payload.items:
        book = session.get(Book, item.book_id)
        if book is None:
            raise NotFound("book", item.book_id)        # nothing committed yet
        if book.stock < item.qty:
            raise Conflict("out_of_stock", f"Only {book.stock} left.")
        book.stock -= item.qty
        session.add(OrderLine(order=order, book=book, qty=item.qty))

    session.commit()          # ← one commit, at the end, for all of it
    return order
```

The rule: **the service decides when a unit of work is complete.** A repository
that commits after every `add()` makes atomicity impossible — the caller can no
longer group two writes.

Rollback belongs in one place too. Either your `get_session` dependency rolls
back on exception, or your service does; doing both is harmless, doing neither
leaves a poisoned session that raises `PendingRollbackError` on its next use:

```python
def get_session() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
```

For nested units of work, `session.begin_nested()` gives you a SAVEPOINT.

## 9. Let the database enforce integrity

Application checks are a nicety; constraints are the guarantee. The check-then-act
race is the reason:

```
request A: SELECT … WHERE isbn = X   → none
request B: SELECT … WHERE isbn = X   → none
request A: INSERT                    → ok
request B: INSERT                    → ok      ← two rows, both "validated"
```

With `unique=True` on the column, request B fails at the database, and your job
is to translate that into the Day 06 envelope:

```python
try:
    session.commit()
except IntegrityError as exc:
    session.rollback()
    if "uq_books_isbn" in str(exc.orig):
        raise Conflict("duplicate_isbn", "A book with this ISBN already exists.")
    raise
```

Also: **SQLite does not enforce foreign keys unless you ask it to.** A dev
environment that silently accepts orphan rows and a production PostgreSQL that
rejects them is a miserable way to spend a Friday:

```python
@event.listens_for(Engine, "connect")
def _fk_on(dbapi_conn, _):
    dbapi_conn.execute("PRAGMA foreign_keys=ON")
```

## 10. Migrations: `create_all()` is not a deployment strategy

```bash
alembic init migrations
alembic revision --autogenerate -m "create books and authors"
alembic upgrade head
alembic downgrade -1
alembic current                      # what is actually deployed
alembic history --verbose
```

`Base.metadata.create_all()` creates tables that do not exist. It cannot add a
column, change a type, or backfill data — so the day after your first release it
is useless, and you are hand-writing `ALTER TABLE` against production.

Rules that keep migrations trustworthy:

1. **Read every autogenerated migration before committing it.** Autogenerate is a
   good first draft. It misses renames (it emits drop + add, destroying data),
   server defaults, `CHECK` constraints, and enum changes.
2. **Commit migrations with the model change**, in the same PR. A model without
   its migration breaks the next person's environment.
3. **Never edit a migration that has run anywhere but your laptop.** Write a new
   one. Editing history means environments silently diverge.
4. **Data migrations need care.** Backfilling a million rows inside a schema
   migration holds a lock for the duration. Batch it, or ship it separately.
5. **Additive first for zero-downtime.** Add a nullable column → deploy code that
   writes it → backfill → add the constraint. Old and new code run simultaneously
   during a rolling deploy; the schema must satisfy both.
6. **Test `downgrade`.** A migration you cannot reverse is a deploy you cannot
   roll back.

## 11. Repositories: keep SQL in one layer

```python
# repositories/books.py
class BookRepository:
    def __init__(self, session: Session):
        self.session = session

    def get(self, book_id: int) -> Book | None:
        return self.session.get(Book, book_id)

    def list(self, *, limit: int, offset: int, q: str | None = None) -> list[Book]:
        stmt = select(Book).order_by(Book.title).limit(limit).offset(offset)
        if q:
            stmt = stmt.where(Book.title.ilike(f"%{q}%"))
        return list(self.session.scalars(stmt))

    def add(self, book: Book) -> Book:
        self.session.add(book)
        self.session.flush()        # assigns the PK — but does NOT commit
        return book
```

`flush()` sends the INSERT and gets the generated id without ending the
transaction; `commit()` ends it. That distinction is what lets a repository
return an object with an `id` while the service still owns the boundary.

Note what the repository does **not** do: no `commit`, no HTTP types, no
Pydantic. Day 10 builds on this.

## 12. Async SQLAlchemy — and why not today

```python
engine = create_async_engine(settings.database_url)   # postgresql+asyncpg://…
async def get_session() -> AsyncIterator[AsyncSession]:
    async with AsyncSessionLocal() as session:
        yield session

result = await session.scalars(select(Book).limit(20))
```

Async is the right choice for high-concurrency, I/O-bound services — and it is
strictly harder: lazy loading raises instead of quietly querying, every call site
needs `await`, and SQLite's async driver is a thin wrapper over a blocking one.

The important thing today: **a sync SQLAlchemy call inside an `async def`
endpoint blocks the entire event loop.** If your session is sync, make the
endpoint `def` (FastAPI runs it in the thread pool) or go async properly. Day 14
covers this in depth; it is the single most common FastAPI performance bug.

## 13. Best practices introduced today

| Practice | Reason |
|---|---|
| SQLAlchemy 2.0 `select()`, not `session.query()` | `Query` is legacy; the docs you find are dated |
| `Mapped[...]` annotations as the schema | nullability lives with the type |
| `Numeric` for money, never `Float` | binary floats cannot represent 0.10 |
| Naming conventions on `MetaData` | constraint names must be stable across backends |
| `CheckConstraint` alongside Pydantic validation | the database survives your bugs |
| One engine per process | an engine per request defeats pooling |
| One session per request, via a `yield` dependency | atomicity, no shared mutable state |
| `pool_pre_ping=True` | idle-killed connections otherwise cost a user a 500 |
| `expire_on_commit=False` | avoids `DetachedInstanceError` while serialising |
| Commit in the service, never in the repository | the caller owns the unit of work |
| Roll back in exactly one place | a poisoned session raises on its next use |
| `flush()` for ids, `commit()` for boundaries | different jobs |
| Rely on `UNIQUE`; translate `IntegrityError` | check-then-insert is a race |
| `PRAGMA foreign_keys=ON` for SQLite | dev must fail the way prod fails |
| Alembic from the first table | `create_all()` cannot alter anything |
| Read and edit autogenerated migrations | autogenerate misses renames and defaults |
| Never edit an applied migration | environments diverge silently |
| Additive migrations for zero downtime | old and new code overlap during a deploy |
| Sync session ⇒ `def` endpoint | a sync call in `async def` stalls the server |
| `index=True` on every column you filter or join on | Day 11 measures this |

## 14. Common mistakes

| Symptom | Cause | Fix |
|---|---|---|
| `QueuePool limit … connection timed out` | sessions not closed | `try/finally` in the dependency |
| `DetachedInstanceError` while serialising | object expired after commit | `expire_on_commit=False` |
| `PendingRollbackError` on the next request | no rollback after a failure | roll back in the dependency |
| Half an order written | commit per repository call | one commit in the service |
| Duplicate rows despite a check | check-then-insert race | `UNIQUE` + catch `IntegrityError` |
| Orphan rows in dev, errors in prod | SQLite FKs off | `PRAGMA foreign_keys=ON` |
| Money off by a cent | `Float` column | `Numeric(10, 2)` |
| Timestamps off by hours | naive `DateTime` | UTC-aware, normalised on output |
| Schema differs between environments | `create_all()` | Alembic |
| Autogenerated migration dropped a column | it read a rename as drop + add | edit the migration |
| Migration works locally, fails in CI | edited an applied revision | new revision instead |
| Deploy cannot be rolled back | `downgrade` never tested | test it |
| Server freezes under load | sync session in `async def` | `def` endpoint, or async engine |
| `session.query()` examples do not fit | 1.x tutorial | use `select()` |
| Every request opens a new pool | engine created per request | module-level engine |
| Slow list endpoints as data grows | no indexes | `index=True`, and Day 11 |
| Objects mysteriously stale | two sessions in one request | one session, injected everywhere |

## 15. Exercises

1. Define `Book` and `Author` with constraints, generate the first migration,
   read it line by line, then `alembic upgrade head` and inspect the SQL schema.
2. Add `session.rollback()` to the dependency, then deliberately raise mid-service
   and confirm the next request works (remove it again and watch
   `PendingRollbackError`).
3. Insert a duplicate ISBN and turn the `IntegrityError` into your Day 06 `409`
   envelope. Then race it: fire twenty concurrent inserts and confirm exactly one
   succeeds.
4. Implement `POST /orders` so a failing second line rolls back the first line's
   stock decrement. Prove it with the `curl` in section 4.
5. Add a `discount_price` column via a migration, deploy-style: nullable first,
   then a backfill, then a constraint. Write down why the order matters.
6. Test `alembic downgrade -1` and then `upgrade head` again. Fix whatever breaks.
7. Turn on `SHELFSPACE_SQL_ECHO`, hit `/books`, and count the queries. Remember
   the number — Day 11 makes it much worse before making it much better.
8. Convert one endpoint to `async def` while keeping the sync session, then
   benchmark it against the `def` version under concurrency. Explain the result.

## 16. What's next

**[Day 10 — CRUD and the Repository Pattern →](../10_crud_and_repository_pattern/)**
The queries work but they are scattered. Tomorrow they get organised: a generic
repository, a service layer that owns transactions and rules, and the pattern
that keeps a growing codebase from turning into SQL in every handler.
