# Day 11 — Relationships and Query Optimization

> **Goal:** model how your data connects, then make it fast — kill the N+1
> problem, choose a loading strategy on purpose, index what you filter on, and
> **measure** instead of guessing.
> **Time:** ~3 hours · **Port:** 8011 · **Builds on:** Day 10

> **Code status:** the README is the spec. Build `shelfspace/` yourself from
> sections 5 onwards before reaching for a reference implementation.

---

## 1. Why this matters

> **The endpoint that killed your database passed code review, passed its tests,
> and looked like three lines of Python.**

```python
books = repo.list(limit=100)
return [BookDetail.model_validate(b) for b in books]   # BookDetail includes author
```

That is **101 queries**: one for the books, then one per book when serialisation
touches `book.author`. On your laptop with 30 rows it takes 4 ms. In production
with 100 rows over a network round trip each, it takes two seconds and holds a
connection for the duration.

Nothing in the Python says "loop over a hundred queries". That is why this day
exists, and why the first tool you build today is a query counter.

## 2. What you will build

```
11_relationships_and_query_optimization/
├── run.py
└── shelfspace/
    ├── db/
    │   ├── models.py        one-to-many, many-to-many, self-referential
    │   └── profiling.py     a query counter you can assert on
    ├── repositories/books.py    loader options live here
    ├── api/v1/books.py          ?include= to keep big relations opt-in
    └── middleware.py            X-Query-Count on every response (dev only)
```

## 3. Run it

```bash
source .venv/bin/activate
cd 11_relationships_and_query_optimization
alembic upgrade head
python -m shelfspace.seed --books 5000 --authors 200    # enough data to hurt
python run.py
```

## 4. Try it — learn by doing

```bash
API=http://127.0.0.1:8011/api/v1

# --- COUNT THE QUERIES. This is the day. ---
curl -sI "$API/books?limit=100&naive=true"  | grep -i x-query-count   # ~101
curl -sI "$API/books?limit=100"             | grep -i x-query-count   # 2
curl -sI "$API/books/1?include=author,reviews" | grep -i x-query-count

# --- and the wall-clock difference ---
curl -s -o /dev/null -w 'naive  %{time_total}s\n' "$API/books?limit=100&naive=true"
curl -s -o /dev/null -w 'loaded %{time_total}s\n' "$API/books?limit=100"

# --- see the SQL: one SELECT, then a hundred, versus one JOIN ---
SHELFSPACE_SQL_ECHO=true python run.py     # then re-run the two calls above

# --- relationship shapes ---
curl -s $API/books/1?include=author        | python -m json.tool   # many-to-one
curl -s $API/authors/1?include=books       | python -m json.tool   # one-to-many
curl -s $API/books/1?include=tags          | python -m json.tool   # many-to-many
curl -s $API/categories/1?include=children | python -m json.tool   # self-referential

# --- big relations are OPT-IN, never default ---
curl -s "$API/authors/1"                | python -m json.tool | wc -c
curl -s "$API/authors/1?include=books"  | python -m json.tool | wc -c

# --- aggregate in SQL, not in Python ---
curl -s $API/authors/stats | python -m json.tool          # one GROUP BY query
curl -sI $API/authors/stats | grep -i x-query-count       # 1

# --- indexes: measure, do not assume ---
sqlite3 shelfspace.db "EXPLAIN QUERY PLAN SELECT * FROM books WHERE isbn='978-0-13-235088-4';"
sqlite3 shelfspace.db "EXPLAIN QUERY PLAN SELECT * FROM books WHERE lower(title)='x';"
# the second cannot use an index on title — a function around a column kills it

# --- pagination that does not degrade ---
curl -s -o /dev/null -w 'offset 0     %{time_total}s\n' "$API/books?limit=20&offset=0"
curl -s -o /dev/null -w 'offset 4000  %{time_total}s\n' "$API/books?limit=20&offset=4000"
curl -s -o /dev/null -w 'cursor       %{time_total}s\n' "$API/books?limit=20&after=4000"
```

Write down the numbers from the first block. Every optimisation today is measured
against them.

## 5. Modelling the four shapes

```python
# many-to-one / one-to-many
class Book(Base):
    author_id: Mapped[int] = mapped_column(ForeignKey("authors.id"), index=True)
    author:    Mapped["Author"] = relationship(back_populates="books")

class Author(Base):
    books: Mapped[list["Book"]] = relationship(
        back_populates="author",
        cascade="all, delete-orphan",     # deleting an author deletes their books
    )

# many-to-many, through an association table
book_tags = Table(
    "book_tags", Base.metadata,
    Column("book_id", ForeignKey("books.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id",  ForeignKey("tags.id",  ondelete="CASCADE"), primary_key=True),
)
class Book(Base):
    tags: Mapped[list["Tag"]] = relationship(secondary=book_tags, back_populates="books")

# many-to-many WITH data on the link → a real model, not a Table
class Review(Base):
    book_id:   Mapped[int] = mapped_column(ForeignKey("books.id"), index=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"), index=True)
    rating:    Mapped[int]
    created_at: Mapped[datetime]

# self-referential
class Category(Base):
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"))
    children:  Mapped[list["Category"]] = relationship(back_populates="parent")
    parent:    Mapped["Category | None"] = relationship(back_populates="children",
                                                       remote_side="Category.id")
```

Four decisions in there:

- **`back_populates` on both sides.** Without it, changing one side leaves the
  other stale in the same session — you set `book.author` and `author.books` does
  not contain the book until a refresh.
- **Index every foreign key.** Most databases index primary keys automatically
  and **not** foreign keys. Every join and every `WHERE author_id = ?` scans
  without it. PostgreSQL also needs it to delete a parent efficiently.
- **`cascade` and `ondelete` are different mechanisms.** `cascade="all,
  delete-orphan"` is SQLAlchemy deleting children in Python; `ondelete="CASCADE"`
  is the database doing it. Use the ORM cascade for correctness in your app, the
  DB one for correctness when anything else touches the table. Choose
  deliberately — a mismatch means orphans or surprise deletions.
- **An association table becomes a model the moment it carries data.** A "rating"
  on the link is not a link any more.

## 6. The N+1 problem, and its three fixes

Lazy loading is SQLAlchemy's default: touching `book.author` runs a `SELECT`
right then. Convenient in a script; catastrophic in a loop.

```python
# selectinload — a second query with WHERE id IN (…)
stmt = select(Book).options(selectinload(Book.author)).limit(100)     # 2 queries

# joinedload — one query with a LEFT OUTER JOIN
stmt = select(Book).options(joinedload(Book.author)).limit(100)       # 1 query

# raiseload — do not query at all; explode instead
stmt = select(Book).options(raiseload("*")).limit(100)
```

| Strategy | Queries | Best for | Watch out |
|---|---|---|---|
| `lazy` (default) | 1 + N | a single object, in a script | the N+1 you are here to kill |
| `selectinload` | 2 | **collections** (one-to-many, many-to-many) | none, mostly — this is the default choice |
| `joinedload` | 1 | **many-to-one** scalars | duplicates rows for collections; breaks `LIMIT` |
| `subqueryload` | 2 | legacy; mostly superseded | slower than `selectinload` |
| `raiseload` | 0 | enforcing intent | raises on any accidental lazy load |

**The `joinedload` + `LIMIT` trap.** Joining a one-to-many multiplies rows: 20
books with 5 tags each is 100 rows, and `LIMIT 20` then returns 20 *rows*, i.e.
about 4 books. SQLAlchemy needs `.unique()` on the result to deduplicate, and your
page size is still wrong. Use `selectinload` for collections; keep `joinedload`
for many-to-one.

**`raiseload("*")` is the strongest tool here.** Turn it on in your test
configuration and every accidental lazy load fails a test instead of shipping:

```python
def test_list_books_is_two_queries(session, query_counter):
    repo.list_with_authors(limit=50)
    assert query_counter.count == 2
```

Set the default per relationship when the answer is always the same:

```python
author: Mapped["Author"] = relationship(lazy="selectin")     # or "raise"
```

## 7. Measure it — a query counter you can assert on

```python
# db/profiling.py
@contextmanager
def count_queries(engine) -> Iterator[Counter]:
    counter = Counter()

    def before(conn, cursor, statement, *_):
        counter.count += 1
        counter.statements.append(statement)

    event.listen(engine, "before_cursor_execute", before)
    try:
        yield counter
    finally:
        event.remove(engine, "before_cursor_execute", before)
```

Expose it in development as a header, so the cost is visible while you work:

```python
@app.middleware("http")
async def query_count_header(request, call_next):
    if not settings.is_production:
        with count_queries(engine) as c:
            response = await call_next(request)
        response.headers["X-Query-Count"] = str(c.count)
        return response
    return await call_next(request)
```

> **A test that asserts a query count is the only reliable defence against N+1.**
> Code review does not catch it — the bad version looks identical to the good one.

Also learn to read a plan:

```sql
EXPLAIN QUERY PLAN SELECT …;              -- SQLite
EXPLAIN (ANALYZE, BUFFERS) SELECT …;      -- PostgreSQL
```

`SCAN books` means a full table scan. `SEARCH books USING INDEX …` means the
index was used. Guessing is not a strategy; the planner will surprise you.

## 8. Indexes: what to add, and what they cost

Index the columns you **filter, join, sort, or enforce uniqueness on**:

```python
isbn:      Mapped[str] = mapped_column(unique=True, index=True)   # lookups
author_id: Mapped[int] = mapped_column(ForeignKey(...), index=True)  # joins
created_at: Mapped[datetime] = mapped_column(index=True)          # sorting

__table_args__ = (
    Index("ix_books_author_created", "author_id", "created_at"),  # composite
)
```

Things people get wrong:

- **Composite index order matters.** `(author_id, created_at)` serves
  `WHERE author_id = ?` and `WHERE author_id = ? ORDER BY created_at`, but not
  `WHERE created_at > ?` alone. Leftmost prefix rule.
- **A function around a column disables the index.** `WHERE lower(title) = 'x'`
  cannot use an index on `title`; you need an expression index
  (`Index("ix_books_title_lower", func.lower(Book.title))`) or a stored normalised
  column. Same for `WHERE title LIKE '%python%'` — a leading wildcard cannot use a
  B-tree at all. Real search needs full-text (`tsvector` in PostgreSQL).
- **Indexes are not free.** Each one slows every `INSERT`/`UPDATE` and consumes
  space. Do not index a low-cardinality column like `is_active` on its own.
- **Adding an index locks the table** unless you say otherwise. In PostgreSQL use
  `CREATE INDEX CONCURRENTLY` in a migration marked non-transactional; on a large
  live table the naive form is an outage.

## 9. Aggregate in SQL, not in Python

```python
# ❌ loads every book to count them
authors = session.scalars(select(Author)).all()
stats = [{"name": a.name, "books": len(a.books)} for a in authors]    # N+1, again

# ✅ one query, the database does the work
stmt = (select(Author.id, Author.name, func.count(Book.id).label("books"),
               func.avg(Book.price).label("avg_price"))
        .join(Book, isouter=True)
        .group_by(Author.id)
        .order_by(func.count(Book.id).desc()))
rows = session.execute(stmt).all()
```

Same for existence and counting:

```python
exists = session.scalar(select(select(Book.id).where(...).exists()))   # ✅
count  = session.scalar(select(func.count()).select_from(Book))        # ✅
count  = len(session.scalars(select(Book)).all())                      # ❌ loads the table
```

And select only what you need for list endpoints:

```python
stmt = select(Book.id, Book.title, Book.price)     # not the 4 KB description column
```

## 10. Pagination at scale: offset vs cursor

```sql
SELECT * FROM books ORDER BY id LIMIT 20 OFFSET 100000;   -- reads 100,020 rows
```

`OFFSET` is not a seek; the database walks and discards every skipped row. Deep
pages get linearly slower, and on a table being written to they are also
*unstable* — an insert shifts everything, so an item can appear on two pages or
none (Day 03).

Cursor (keyset) pagination fixes both:

```python
stmt = select(Book).order_by(Book.id).limit(limit)
if after is not None:
    stmt = stmt.where(Book.id > after)          # a seek, not a scan
```

| | Offset | Cursor |
|---|---|---|
| Deep page cost | O(offset) | O(limit) |
| Stability under writes | items skip and repeat | stable |
| "Jump to page 50" | trivial | not supported |
| Total count | easy | usually omitted (`COUNT(*)` is also O(n)) |

Use offset for small admin tables where users jump around; cursor for feeds,
exports and anything large. Sort by a **unique** column, or a tuple ending in one
(`(created_at, id)`), or ties break the ordering.

## 11. Keep large relations opt-in

```python
@router.get("/authors/{id}")
async def get_author(id: int, include: Annotated[set[str], Query()] = set()):
    options = []
    if "books" in include:
        options.append(selectinload(Author.books))
    ...
```

An author with 400 books should not ship 400 books because one client wanted a
name. Make it explicit — `?include=books` — and cap what a single response can
embed. This is also how you avoid the "helpful" default that becomes a permanent
part of your contract.

For a deeply nested `include`, remember every level multiplies: `?include=
books.reviews.member` on 50 authors is a lot of rows even with perfect eager
loading. Limit include depth, and say so in the docs.

## 12. Best practices introduced today

| Practice | Reason |
|---|---|
| Count queries per endpoint, in development | N+1 is invisible in the Python |
| Assert query counts in tests | the only defence that survives refactoring |
| `selectinload` for collections | 2 queries, no row multiplication |
| `joinedload` for many-to-one scalars | 1 query, no duplication risk |
| Never `joinedload` a collection with `LIMIT` | `LIMIT` counts joined rows, not entities |
| `raiseload("*")` in tests | accidental lazy loads fail loudly |
| `back_populates` on both sides | stale in-session state otherwise |
| Index every foreign key | they are not indexed automatically |
| Composite indexes follow the leftmost-prefix rule | column order decides usefulness |
| Avoid functions around indexed columns | it disables the index |
| `EXPLAIN` before and after | the planner ignores your intentions |
| Aggregate with `GROUP BY`, not Python loops | one query instead of thousands |
| `exists()` and `count()` in SQL | do not load rows to count them |
| Select only needed columns for lists | payload and memory |
| Cursor pagination for large or live tables | `OFFSET` degrades and is unstable |
| Big relations opt-in via `?include=` | one client's need is not everyone's payload |
| `CREATE INDEX CONCURRENTLY` on live tables | the naive form locks writes |
| Decide ORM cascade vs DB `ondelete` deliberately | a mismatch orphans or over-deletes |

## 13. Common mistakes

| Symptom | Cause | Fix |
|---|---|---|
| Endpoint fine locally, slow in prod | N+1 with network latency per query | eager load |
| 101 queries for 100 rows | lazy loading during serialisation | `selectinload` |
| `LIMIT 20` returns 4 objects | `joinedload` on a collection | `selectinload` |
| Duplicate rows in the result | joined collection without `.unique()` | `selectinload`, or `.unique()` |
| `DetachedInstanceError` in a response | lazy load after the session closed | eager load, or `expire_on_commit=False` |
| Slow `WHERE author_id = ?` | foreign key not indexed | `index=True` |
| Index exists but is unused | function or leading wildcard in the predicate | expression index / full-text |
| Writes got slower | too many indexes | drop the unused ones |
| Migration locked the table for minutes | plain `CREATE INDEX` | `CONCURRENTLY` |
| Page 500 takes 8 seconds | deep `OFFSET` | cursor pagination |
| Items skip or repeat between pages | offset paging on a changing table | cursor + unique sort key |
| `COUNT(*)` dominates the request | counting a huge table every page | estimate, or drop `total` |
| Deleting an author fails | children reference it, no cascade | `ondelete` / ORM cascade |
| Children silently deleted | cascade you did not intend | audit `cascade=` |
| Author payload is 2 MB | relation loaded by default | `?include=` |
| Stale `author.books` after an assignment | missing `back_populates` | add it |
| Memory spike on an export | loaded the whole table | `yield_per`, streaming (Day 19) |

## 14. Exercises

1. Build the query counter and the `X-Query-Count` header, then reproduce the
   101-query result. Keep both numbers.
2. Fix it with `selectinload`, then try `joinedload` on `Book.tags` with
   `LIMIT 20` and explain why you get the wrong number of books.
3. Turn on `raiseload("*")` in the test config and fix everything that breaks.
   Write a test asserting `/books?limit=50` is exactly 2 queries.
4. `EXPLAIN QUERY PLAN` three queries: by `isbn`, by `lower(title)`, and by
   `title LIKE '%python%'`. Record which use an index and why.
5. Add a composite index on `(author_id, created_at)` and find a query it helps
   and one it does not, using the leftmost-prefix rule.
6. Rewrite `/authors/stats` from a Python loop to one `GROUP BY` and compare
   query counts and time at 200 authors.
7. Implement `?after=<id>` cursor pagination and benchmark it against
   `offset=4000` on 5,000 rows. Then argue when offset is still the right choice.
8. Add `?include=books,reviews` with a maximum include depth, and decide what
   happens when a client asks for more.

## 15. What's next

**[Day 12 — REST API Design and Pagination →](../12_rest_api_design_and_pagination/)**
Your API is now fast and correct. Tomorrow it becomes *predictable*: resource
design, one pagination envelope with links, filtering and sorting from an
allow-list, versioning, and ETags for conditional requests and optimistic
locking.
