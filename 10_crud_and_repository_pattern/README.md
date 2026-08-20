# Day 10 — CRUD and the Repository Pattern

> **Goal:** stop repeating the same five queries for every resource — a generic
> repository, a service layer that owns transactions and business rules, and a
> clear answer to "where does this code go?"
> **Time:** ~2.5 hours · **Port:** 8010 · **Builds on:** Day 09

> **Code status:** the README is the spec. Build `shelfspace/` yourself from
> sections 5 onwards before reaching for a reference implementation.

---

## 1. Why this matters

> **Copy-pasted CRUD is not a style problem. It is where inconsistency lives.**

Five resources, five hand-written `list()` methods. Four of them clamp `limit`;
the fifth does not. Three order results deterministically; two return rows in
whatever order the database felt like, so page 2 sometimes repeats page 1. Two
soft-delete; three hard-delete. Nobody decided any of this — it accumulated.

A generic repository makes the boring 80% identical by construction, so the
interesting 20% is the only thing you have to read.

## 2. What you will build

```
10_crud_and_repository_pattern/
├── run.py
└── shelfspace/
    ├── repositories/
    │   ├── base.py          BaseRepository[Model] — the generic five
    │   ├── books.py         BookRepository — only what is special
    │   └── authors.py
    ├── services/
    │   ├── catalogue.py     rules + transaction boundaries
    │   └── orders.py        a multi-step unit of work
    ├── api/v1/books.py      thin: parse, call, status code
    └── db/…                 (Day 09)
```

## 3. Run it

```bash
source .venv/bin/activate
cd 10_crud_and_repository_pattern
alembic upgrade head && python -m shelfspace.seed
python run.py
```

## 4. Try it — learn by doing

```bash
API=http://127.0.0.1:8010/api/v1
JSON='Content-Type: application/json'

# --- the same five operations, identical semantics, two resources ---
curl -s "$API/books?limit=2&offset=0"    | python -m json.tool
curl -s "$API/authors?limit=2&offset=0"  | python -m json.tool

# --- deterministic ordering: page 2 never repeats page 1 ---
curl -s "$API/books?limit=2&offset=0" | python -c "import json,sys;print([b['id'] for b in json.load(sys.stdin)['items']])"
curl -s "$API/books?limit=2&offset=2" | python -c "import json,sys;print([b['id'] for b in json.load(sys.stdin)['items']])"

# --- PATCH touches only what was sent ---
curl -s  $API/books/1 | python -m json.tool
curl -sX PATCH $API/books/1 -H "$JSON" -d '{"stock":42}' | python -m json.tool

# --- business rules live in the service, and speak the Day 06 envelope ---
curl -sX POST $API/books/1/borrow -H "$JSON" -d '{"days":14}'  | python -m json.tool
curl -sX POST $API/books/3/borrow -H "$JSON" -d '{"days":14}'  | python -m json.tool  # 409 out_of_stock
curl -sX POST $API/books/1/borrow -H "$JSON" -d '{"days":400}' | python -m json.tool  # 422

# --- one unit of work: three writes, or none ---
curl -sX POST $API/orders -H "$JSON" -d '{"items":[
  {"book_id":1,"qty":1},{"book_id":2,"qty":1},{"book_id":9999,"qty":1}]}' | python -m json.tool
curl -s $API/books/1 | python -c "import json,sys;print('stock =',json.load(sys.stdin)['stock'])"

# --- soft delete: gone from the API, present in the table ---
curl -isX DELETE $API/books/2 | head -3
curl -s  $API/books/2 -o /dev/null -w '%{http_code}\n'
curl -s "$API/books?include_deleted=true" | grep -c '"id"'
sqlite3 shelfspace.db "SELECT id, title, deleted_at FROM books WHERE id = 2;"

# --- the service is callable WITHOUT HTTP ---
python -c "
from shelfspace.db.session import SessionLocal
from shelfspace.services import catalogue
with SessionLocal() as s:
    print(catalogue.list_books(s, limit=3, offset=0))"
```

That last command is the real test of your layering. If the service needs a
`Request`, it is not a service.

## 5. The generic repository

```python
# repositories/base.py
ModelT = TypeVar("ModelT", bound=Base)

class BaseRepository(Generic[ModelT]):
    model: type[ModelT]

    def __init__(self, session: Session):
        self.session = session

    def get(self, id_: int) -> ModelT | None:
        return self.session.get(self.model, id_)

    def list(self, *, limit: int = 20, offset: int = 0,
             order_by: InstrumentedAttribute | None = None) -> list[ModelT]:
        stmt = (select(self.model)
                .order_by(order_by if order_by is not None else self.model.id)
                .limit(limit).offset(offset))
        return list(self.session.scalars(stmt))

    def count(self) -> int:
        return self.session.scalar(
            select(func.count()).select_from(self.model)) or 0

    def add(self, obj: ModelT) -> ModelT:
        self.session.add(obj)
        self.session.flush()            # assigns the PK; does NOT commit
        return obj

    def delete(self, obj: ModelT) -> None:
        self.session.delete(obj)
```

Then the specific one adds only what is genuinely specific:

```python
# repositories/books.py
class BookRepository(BaseRepository[Book]):
    model = Book

    def by_isbn(self, isbn: str) -> Book | None:
        return self.session.scalar(select(Book).where(Book.isbn == isbn))

    def search(self, q: str, *, limit: int, offset: int) -> list[Book]:
        stmt = (select(Book)
                .where(or_(Book.title.ilike(f"%{q}%"), Book.isbn == q))
                .order_by(Book.title, Book.id)
                .limit(limit).offset(offset))
        return list(self.session.scalars(stmt))
```

Three rules keep this honest:

- **`.order_by()` on every list query.** Without `ORDER BY`, SQL returns rows in
  *no defined order*. It looks stable on 30 rows and stops being stable the day
  the planner picks a different path — then `LIMIT/OFFSET` paging silently
  duplicates and skips rows. Order by something **unique** (or append `id`) so
  ties cannot reorder between requests.
- **No `commit()` in a repository.** It owns queries, not units of work.
- **Return model objects, not schemas.** Pydantic conversion belongs at the HTTP
  boundary; a repository that returns `BookPublic` cannot be used by a worker
  that needs the ORM object.

## 6. Update: read, mutate, let the session notice

```python
def update(self, obj: ModelT, changes: dict) -> ModelT:
    for field, value in changes.items():
        setattr(obj, field, value)
    self.session.flush()
    return obj
```

Combined with Day 02's `exclude_unset`, `PATCH` becomes exact:

```python
changes = payload.model_dump(exclude_unset=True)     # ONLY what the client sent
book = repo.update(book, changes)
```

SQLAlchemy tracks which attributes changed and emits an `UPDATE` touching only
those columns. Two warnings:

- **Never build `setattr` targets from raw client input.** `changes` must come
  from a Pydantic model, whose fields you defined. `setattr(obj, key, value)` over
  an arbitrary dict is mass assignment — the client picks the column.
- **Bulk updates skip the ORM.** `session.execute(update(Book).where(...))` is
  fast and does **not** run Python-side validators, `onupdate` defaults, or
  events. That is fine when you know it; it is a mystery when you do not.

## 7. Delete: hard, or soft, but decide

```python
class SoftDeleteMixin:
    deleted_at: Mapped[datetime | None] = mapped_column(default=None, index=True)

# every read must then exclude them — this is the cost
stmt = select(Book).where(Book.deleted_at.is_(None))
```

| | Hard delete | Soft delete |
|---|---|---|
| Row | gone | flagged |
| Restore | from a backup | one `UPDATE` |
| Foreign keys | may cascade or block | untouched |
| Reads | simple | **every query needs the filter** |
| Uniqueness | free | `UNIQUE(isbn)` now blocks re-adding a deleted ISBN |
| GDPR erasure | done | not done — the data is still there |

Soft delete is not free, and the two rows in bold are where it hurts. If you
choose it: put the filter in the repository (so no handler can forget it), use a
partial unique index (`UNIQUE(isbn) WHERE deleted_at IS NULL`), and keep a real
purge path for erasure requests.

Whichever you choose, apply it to the whole codebase. A mix means "deleted" means
different things per resource.

## 8. The service layer: rules, orchestration, transactions

```python
# services/catalogue.py
def borrow_book(session: Session, book_id: int, member_id: int, days: int) -> Loan:
    books = BookRepository(session)
    loans = LoanRepository(session)

    book = books.get(book_id)
    if book is None:
        raise NotFound("book", book_id)
    if book.stock < 1:
        raise Conflict("out_of_stock", "No copies available.", isbn=book.isbn)
    if loans.active_count(member_id) >= MAX_ACTIVE_LOANS:
        raise Conflict("loan_limit_reached",
                       f"A member may hold {MAX_ACTIVE_LOANS} books at a time.")

    book.stock -= 1
    loan = loans.add(Loan(book_id=book.id, member_id=member_id,
                          due_at=utcnow() + timedelta(days=days)))
    session.commit()                       # ← the boundary, here and nowhere else
    return loan
```

What makes this a service rather than a handler with extra steps:

| It does | It never does |
|---|---|
| enforces rules across entities | touch `Request` or `Response` |
| coordinates several repositories | raise `HTTPException` |
| owns `commit` / rollback | choose status codes |
| is callable from a worker, CLI or test | build URLs |

It raises `APIError` subclasses (Day 06), which the error handler maps to HTTP —
so the same function works unchanged inside Day 18's background worker.

**Where to put a rule?**

| Rule | Layer |
|---|---|
| `price > 0`, ISBN format | schema (Pydantic) |
| `stock >= 0` always true | database `CHECK` |
| "cannot borrow when out of stock" | service |
| "at most 5 active loans" | service |
| `UNIQUE(isbn)` | database |
| "only the owner may edit" | service (Day 16) |

## 9. Wiring it up with dependencies

```python
# api/deps.py
def get_book_repo(session: SessionDep) -> BookRepository:
    return BookRepository(session)

BookRepoDep = Annotated[BookRepository, Depends(get_book_repo)]

# api/v1/books.py
@router.get("", response_model=Page[BookPublic])
async def list_books(repo: BookRepoDep, page: PaginationDep, q: str | None = None):
    items = repo.search(q, limit=page.limit, offset=page.offset) if q \
            else repo.list(limit=page.limit, offset=page.offset)
    return Page(items=items, total=repo.count(), limit=page.limit, offset=page.offset)
```

Because every repository shares one injected session (Day 08's caching), a
service that instantiates three repositories still runs in **one** transaction.

Note the endpoint is four lines and contains no SQL, no rules, and no error
handling. That is the target.

## 10. Do not build a Unit of Work you do not need

You will read about a `UnitOfWork` class that owns the session and exposes
repositories:

```python
with UnitOfWork() as uow:
    uow.books.add(book)
    uow.commit()
```

In FastAPI, the request-scoped session dependency **already is** your unit of
work: one session, one transaction, injected everywhere, overridable in tests. A
`UnitOfWork` wrapper on top mostly re-implements what `Depends` gave you.

It earns its place when you need transactions **outside** a request — a worker
processing a queue, a CLI command, a scheduled job — and you want one idiom
everywhere. Build it then, not now.

Similarly, resist the urge to abstract the ORM behind a "database-agnostic"
interface. You will not swap PostgreSQL for MongoDB, and the abstraction costs
you the ORM's best features (eager loading, Day 11) while pretending to a
portability nobody will use.

## 11. Testing what you just built

The layers pay off here:

```python
def test_borrow_rejects_when_out_of_stock(session):
    book = BookFactory(stock=0)
    with pytest.raises(Conflict) as exc:
        catalogue.borrow_book(session, book.id, member_id=1, days=7)
    assert exc.value.code == "out_of_stock"
```

No HTTP, no client, no JSON — a business rule tested as a business rule, in
milliseconds. Then one API test per endpoint confirms the wiring: status code,
envelope, and that a rule violation surfaces as `409`.

That split — many fast service tests, a few API tests — is what keeps a suite
fast enough to run on every save. Day 17 builds it properly.

## 12. Best practices introduced today

| Practice | Reason |
|---|---|
| A generic `BaseRepository` for the boring five | consistency by construction |
| Subclasses hold only what is special | the interesting code is the only code you read |
| `ORDER BY` on **every** list query | unordered SQL breaks pagination silently |
| Order by a unique column (or append `id`) | ties reorder between requests |
| Never `commit()` in a repository | the caller owns the unit of work |
| Repositories return ORM objects | workers and services need them, not schemas |
| `exclude_unset` + `setattr` from a validated model | partial update without mass assignment |
| Choose hard or soft delete once, globally | otherwise "deleted" means five things |
| Soft delete ⇒ filter inside the repository | no handler can forget it |
| Services own rules, transactions and orchestration | reusable outside HTTP |
| Services raise `APIError`, never `HTTPException` | callable from a worker or CLI |
| Rules at the right layer (schema / service / DB) | each catches what the others cannot |
| Repositories built from the injected session | one transaction across many repositories |
| Skip `UnitOfWork` until you need non-HTTP transactions | `Depends` already provides it |
| Do not abstract the ORM "for portability" | you lose eager loading and gain nothing |
| Test services directly; test endpoints thinly | fast suite, real coverage |

## 13. Common mistakes

| Symptom | Cause | Fix |
|---|---|---|
| Page 2 repeats an item from page 1 | no `ORDER BY`, or a non-unique one | order by a unique column |
| Partial writes committed | commit inside repository methods | commit once, in the service |
| `PATCH` wipes unsent fields | `model_dump()` without `exclude_unset` | add it |
| Client set a column it should not | `setattr` over a raw dict | only from a validated model |
| Validators skipped on a bulk update | `update()` statement bypasses the ORM | use ORM objects, or accept it knowingly |
| Deleted rows still appear | forgot the soft-delete filter | filter in the repository |
| Cannot re-add a deleted ISBN | `UNIQUE` counts deleted rows | partial unique index |
| "Deleted" data returned to a GDPR request | soft delete is not erasure | real purge path |
| Service needs a `Request` | HTTP leaked downward | pass plain arguments |
| Worker cannot reuse a rule | rule lives in the handler | move it to the service |
| Three transactions in one request | repositories building their own sessions | inject one session |
| `409` becomes `500` | service raised a bare exception | raise `APIError` subclasses |
| Repository returns Pydantic models | conversion too low | convert at the HTTP boundary |
| Ten layers to add one field | abstraction without a second caller | collapse it |
| Slow tests | everything tested through HTTP | test services directly |
| N+1 queries on list endpoints | relationships loaded lazily | Day 11 |

## 14. Exercises

1. Write `BaseRepository` with the five operations, then implement
   `BookRepository` and `AuthorRepository` on top. Count how many lines the second
   one needed.
2. Remove `ORDER BY` from `list()`, insert 10,000 rows, and page through them
   until you can demonstrate a duplicate or a skipped row.
3. Implement `borrow_book` with all three rules from section 8 and test it
   *without* HTTP, as in section 11.
4. Implement `POST /orders` as one unit of work across three repositories, and
   prove atomicity with the `curl` in section 4.
5. Add soft delete to `Book`: mixin, repository filter, partial unique index, and
   an `?include_deleted=true` escape hatch for admins. Then list everything the
   change touched.
6. Move `MAX_ACTIVE_LOANS` into settings and confirm the service still needs no
   HTTP import.
7. Write a `UnitOfWork` and then delete it, noting exactly which line `Depends`
   already gave you.
8. Add `repo.upsert(isbn, **fields)` and decide whether the "does it exist" check
   belongs there or in the database (Day 09, section 9).

## 15. What's next

**[Day 11 — Relationships and Query Optimization →](../11_relationships_and_query_optimization/)**
Your list endpoint is clean and, if it returns authors, secretly running 101
queries. Tomorrow: relationships, the N+1 problem, eager loading strategies,
indexes, and how to measure all of it instead of guessing.
