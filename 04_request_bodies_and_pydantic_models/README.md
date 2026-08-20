# Day 04 — Request Bodies and Pydantic Models

> **Goal:** accept structured JSON safely — nested models, field constraints,
> custom and cross-field validators — so that an invalid book cannot be
> constructed, let alone stored.
> **Time:** ~2.5 hours · **Port:** 8004 · **Builds on:** Day 03

> **Code status:** the README is the spec. Build `shelfspace/` yourself from
> sections 5 onwards before reaching for a reference implementation.

---

## 1. Why this matters

> **Validation is not a chore you do before the real work. It is the boundary
> between your assumptions and the internet.**

Hand-written validation always follows the same arc: a few `if` statements, then
twenty, then a 90-line `_validate_book()` that returns on the *first* error, so
the client fixes one field per round trip and hates you by the fifth.

Pydantic replaces the whole file with a class where the types **are** the rules,
collects **every** error at once, and hands FastAPI an OpenAPI schema for free.

## 2. What you will build

Book creation that survives contact with real payloads:

```
04_request_bodies_and_pydantic_models/
├── run.py
└── shelfspace/
    ├── config.py
    ├── data.py
    ├── schemas.py      the whole lesson lives here
    │   ├── Money            a reusable constrained type
    │   ├── Author           a nested model
    │   ├── Dimensions       another nested model
    │   ├── BookCreate       field constraints + validators
    │   ├── BookReplace / BookPatch
    │   └── BulkCreate       a list body, bounded
    └── main.py         thin handlers — the models do the work
```

## 3. Run it

```bash
source .venv/bin/activate
cd 04_request_bodies_and_pydantic_models
python run.py
```

In <http://127.0.0.1:8004/docs>, expand `POST /books` → **Schema**. Every
constraint you declare — minimum length, pattern, bounds, defaults, examples — is
published there. The docs cannot drift from the validation because they are
generated from it.

## 4. Try it — learn by doing

```bash
API=http://127.0.0.1:8004
JSON='Content-Type: application/json'

# --- a valid, fully nested book ---
curl -sX POST $API/books -H "$JSON" -d '{
  "isbn": "978-1-4919-4600-8",
  "title": "Fluent Python",
  "subtitle": "Clear, Concise, and Effective Programming",
  "price": "4199.00",
  "stock": 7,
  "published_year": 2022,
  "tags": ["python", "reference"],
  "author": {"name": "Luciano Ramalho", "email": "luciano@example.com"},
  "dimensions": {"width_mm": 178, "height_mm": 233, "pages": 1012}
}' | python -m json.tool

# --- ALL the errors at once, not the first one ---
curl -sX POST $API/books -H "$JSON" -d '{
  "isbn": "nope", "title": "", "price": "-5",
  "stock": -2, "published_year": 3000,
  "author": {"name": "x", "email": "not-an-email"}
}' | python -m json.tool

# --- the shape of a validation error: loc / msg / type ---
curl -sX POST $API/books -H "$JSON" -d '{"title": 42}' | python -m json.tool

# --- errors inside a nested model point INTO the nesting ---
curl -sX POST $API/books -H "$JSON" \
  -d '{"isbn":"978-1-4919-4600-8","title":"T","price":"10.00","stock":1,
       "author":{"name":"","email":"a@b.com"}}' | python -m json.tool
# loc: ["body", "author", "name"]

# --- cross-field rules a single field cannot express ---
curl -sX POST $API/books -H "$JSON" \
  -d '{"isbn":"978-1-4919-4600-8","title":"T","price":"100.00","stock":0,
       "author":{"name":"A","email":"a@b.com"},
       "discount_price":"200.00"}' | python -m json.tool   # discount > price

# --- coercion: what Pydantic will and will not do for you ---
curl -sX POST $API/echo -H "$JSON" -d '{"stock": "7"}'    | python -m json.tool  # "7" → 7
curl -sX POST $API/echo -H "$JSON" -d '{"stock": 7.0}'    | python -m json.tool  # ok
curl -sX POST $API/echo -H "$JSON" -d '{"stock": 7.5}'    | python -m json.tool  # 422
curl -sX POST $API/echo -H "$JSON" -d '{"stock": true}'   | python -m json.tool  # 422

# --- extra fields: silently dropped, or rejected? Your choice (section 11) ---
curl -sX POST $API/books -H "$JSON" -d '{"isbn":"978-1-4919-4600-8","title":"T",
  "price":"10.00","stock":1,"author":{"name":"A","email":"a@b.com"},
  "is_admin": true}' | python -m json.tool

# --- the wrong Content-Type, and broken JSON, are different failures ---
curl -sX POST $API/books -d 'title=x'                | python -m json.tool
curl -sX POST $API/books -H "$JSON" -d '{"title":'   | python -m json.tool
```

The second command is the point of the day. Five mistakes, one response, one
round trip.

## 5. A model is a contract, not a container

```python
class BookCreate(BaseModel):
    isbn: str = Field(pattern=r"^97[89]-\d-\d{2,5}-\d{2,7}-\d$")
    title: str = Field(min_length=1, max_length=200)
    price: Decimal = Field(gt=0, max_digits=8, decimal_places=2)
    stock: int = Field(ge=0, default=0)
    published_year: int = Field(ge=1450, le=2100)
    author: Author                      # nested model, validated too
    tags: list[str] = Field(default_factory=list, max_length=10)
```

Reading that class tells you exactly what the endpoint accepts — and it *is* the
enforcement, so it cannot be out of date. Declaring it buys you five things:

| | |
|---|---|
| **Parsing** | JSON → typed Python objects |
| **Validation** | every constraint, every field, every request |
| **Error reporting** | `422` naming each field, with a machine-readable `type` |
| **Documentation** | the JSON Schema in `/docs` and `/openapi.json` |
| **Editor support** | `book.author.name` autocompletes and type-checks |

FastAPI decides a parameter is the **request body** because its type is a
Pydantic model — not from a decorator argument. Scalars are query parameters
(Day 03); models come from the body.

## 6. `Field()` — the constraints worth knowing

| Constraint | Applies to | Notes |
|---|---|---|
| `gt` `ge` `lt` `le` | numbers | `gt=0` for money; `ge=0` for counts |
| `min_length` `max_length` | strings, lists | on a list it bounds the item count |
| `pattern` | strings | anchor it with `^…$` or it matches a substring |
| `multiple_of` | numbers | e.g. quantities sold in packs |
| `max_digits` `decimal_places` | `Decimal` | the money constraint |
| `default_factory` | anything mutable | **never** `default=[]` |
| `description` `examples` | all | goes straight into `/docs` |
| `alias` | all | accept `bookTitle`, store `title` |

Two of these cause real bugs when skipped.

**`default_factory=list`, never `default=[]`.** A mutable default is evaluated
once and shared by every instance — the classic Python trap. Pydantic actually
guards against it, but `default_factory` is the habit that also works in dataclasses
and plain functions.

**Anchor your patterns.** `pattern=r"\d{5}"` matches `"abc12345xyz"`. Pydantic v2
uses `re.search` semantics unless you anchor, and an unanchored pattern is a
validation rule that quietly does nothing.

## 7. Nested models: validation goes all the way down

```python
class Author(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    website: HttpUrl | None = None

class Dimensions(BaseModel):
    width_mm: int = Field(gt=0, le=1000)
    height_mm: int = Field(gt=0, le=1000)
    pages: int = Field(gt=0, le=10_000)

class BookCreate(BaseModel):
    author: Author
    dimensions: Dimensions | None = None
    contributors: list[Author] = Field(default_factory=list, max_length=20)
```

The error location follows the nesting exactly:

```json
{"detail": [{
  "loc": ["body", "contributors", 2, "email"],
  "msg": "value is not a valid email address",
  "type": "value_error"
}]}
```

`["body", "contributors", 2, "email"]` — body → field → **index 2** → field. A
client can highlight the precise input. Hand-written validation essentially never
produces this, which is why hand-written validation gets a generic error toast.

`EmailStr` needs `email-validator` installed (it is in `requirements.txt`).
`HttpUrl` rejects `javascript:alert(1)` — worth remembering the day you render a
stored URL in a browser.

## 8. Custom validators — the two you need

**`field_validator`** — one field, arbitrary logic:

```python
@field_validator("title")
@classmethod
def strip_and_require(cls, v: str) -> str:
    v = v.strip()
    if not v:
        raise ValueError("Title cannot be blank or whitespace.")
    return v          # ALWAYS return the value — returning None wipes the field

@field_validator("isbn")
@classmethod
def normalise_isbn(cls, v: str) -> str:
    return v.replace(" ", "").replace("–", "-").upper()
```

`mode="before"` runs on the **raw input** (use it to normalise `"1,299.00"`
before `Decimal` sees it); the default `mode="after"` runs on the parsed,
type-correct value.

**`model_validator(mode="after")`** — rules that need more than one field:

```python
@model_validator(mode="after")
def discount_below_price(self) -> "BookCreate":
    if self.discount_price is not None and self.discount_price >= self.price:
        raise ValueError("discount_price must be lower than price.")
    return self
```

This is the answer to Day 03's exercise 3: `min_price > max_price` cannot be
expressed as a single-field constraint, and does not belong in the handler either.

> **Raise `ValueError`, not `HTTPException`.** Pydantic catches `ValueError` and
> folds it into the `422` alongside every other field error. An `HTTPException`
> escapes the validation pass, aborts on the first problem, and loses the rest.

## 9. Three models per resource, not one

```python
class BookCreate(BaseModel):    # what a client may SEND
    ...
class BookReplace(BaseModel):   # PUT — every field required
    ...
class BookPatch(BaseModel):     # PATCH — every field optional
    ...
class Book(BaseModel):          # what the server RETURNS (Day 05)
    id: int
    created_at: datetime
```

Using one model for input and output is the most common structural mistake in
FastAPI codebases, and it fails in both directions:

- **Inbound**, it lets a client send `id`, `created_at`, or — the one that ends
  up in an incident report — `is_admin`. Anything the model accepts, an attacker
  may try to set.
- **Outbound**, it forces server-assigned fields to be `Optional` so the create
  path type-checks, and now every consumer must handle `id: None`, which never
  actually happens.

The duplication is real and it is worth it. Day 05 shows how a small inheritance
chain removes most of it.

## 10. Coercion: helpful, and occasionally surprising

| Input | Declared | Result |
|---|---|---|
| `"7"` | `int` | `7` — strings that look like ints are coerced |
| `7.0` | `int` | `7` — a lossless float is fine |
| `7.5` | `int` | **422** — Pydantic v2 refuses lossy coercion |
| `true` | `int` | **422** — `bool` is not silently an `int` |
| `"4199.00"` | `Decimal` | `Decimal("4199.00")` — exact |
| `4199.00` | `Decimal` | works, but the float already lost precision |
| `"2024-01-15"` | `date` | parsed |
| `null` | `str` | **422** unless the type is `str \| None` |

Pydantic v2 is stricter than v1 — `7.5 → 7` and `True → 1` were v1 behaviours
that hid real bugs. If you need absolute strictness, `model_config =
ConfigDict(strict=True)` disables coercion entirely; most APIs want the default
*lax* mode, because HTTP is a world of strings.

**Money:** annotate `Decimal` and have clients send it as a **string**.
`Decimal(4199.00)` from a JSON float inherits the float's error before Pydantic
ever sees it.

## 11. Extra fields: drop, ignore, or forbid

```python
model_config = ConfigDict(extra="ignore")   # default: silently dropped
model_config = ConfigDict(extra="forbid")   # 422 naming the unexpected field
model_config = ConfigDict(extra="allow")    # kept — almost never what you want
```

`ignore` is the safe default: unknown fields are dropped, so a client that sends
`{"is_admin": true}` achieves nothing.

`forbid` is better for **internal** APIs, because it turns `{"titel": "x"}` into
an immediate, explicit error instead of a book created with the default title.
The trade-off is the usual one: strictness catches typos and breaks clients that
add fields, so version your API (Day 12) before you turn it on.

`allow` keeps arbitrary client data on the model. If that data then reaches a
database or a template, you have handed a stranger a pen.

## 12. Bodies that are not a single object

```python
# a list body, BOUNDED — an unbounded list body is a memory DoS
@app.post("/books/bulk")
async def bulk_create(books: Annotated[list[BookCreate], Body(max_length=100)]): ...

# two models in one body → FastAPI nests them by parameter name
@app.post("/orders")
async def create_order(book: BookCreate, customer: Customer): ...
# {"book": {...}, "customer": {...}}

# a scalar that must live in the body, not the query string
@app.post("/books/{id}/notes")
async def add_note(id: int, note: Annotated[str, Body(embed=True)]): ...
# {"note": "..."}  — `embed=True` is what makes it an object, not a bare string
```

Also cap the body itself. Pydantic validates *after* the payload is read, so a
200 MB upload is already in memory by then. Limit it at the proxy (nginx
`client_max_body_size`) or in middleware (Day 13).

## 13. Best practices introduced today

| Practice | Reason |
|---|---|
| Declare a model; never hand-roll validation | one class replaces ninety lines and reports every error |
| Separate Create / Replace / Patch / Response models | input ≠ output; mass-assignment is a real attack |
| `Field(...)` constraints over `if` checks in handlers | enforced *and* documented in one place |
| `default_factory` for mutable defaults | shared mutable state is a classic bug |
| Anchor regex patterns with `^…$` | an unanchored pattern validates nothing |
| `EmailStr` / `HttpUrl` over hand-written regex | email regexes are wrong; these are not |
| Raise `ValueError` in validators | it joins the 422; `HTTPException` escapes it |
| Always `return` the value from a validator | returning `None` silently blanks the field |
| `model_validator(mode="after")` for cross-field rules | one field cannot see another |
| `Decimal` for money, sent as a string | binary floats cannot represent 0.10 |
| Choose `extra` deliberately | `ignore` for public, `forbid` for internal |
| Bound list bodies with `max_length` | an unbounded list is a memory DoS |
| `description` and `examples` on every field | `/docs` becomes the reference |
| Keep handlers thin | if the model validated it, the handler need not |

## 14. Common mistakes

| Symptom | Cause | Fix |
|---|---|---|
| Body arrives as a query parameter | annotated as a scalar | use a model, or `Body(embed=True)` |
| `422` with `loc: ["body"]` and nothing else | malformed JSON | check the payload, not the model |
| Client can set `id` or `is_admin` | one model for input and output | separate them |
| `id: int \| None` everywhere | reusing the response model on input | separate them |
| Validator returns `None`, field blanked | forgot to `return v` | return the value |
| Only the first error is reported | raised `HTTPException` in a validator | raise `ValueError` |
| `pattern` never rejects anything | unanchored regex | `^…$` |
| `12.10` becomes `12.099999999999` | `float` for money | `Decimal`, sent as a string |
| `{"stock": true}` stores `1` | Pydantic v1 habits | v2 rejects it — do not "fix" that |
| Typo'd field silently ignored | `extra="ignore"` | `extra="forbid"` for internal APIs |
| Every instance shares one list | `default=[]` | `default_factory=list` |
| `EmailStr` raises `ImportError` | `email-validator` missing | it is in `requirements.txt` |
| Nested errors are unreadable | ignoring `loc` | `loc` is a path — walk it |
| 100 MB body OOMs the worker | validation happens after reading | cap at the proxy / middleware |
| `Optional[str]` still required (v1 habit) | v2 needs an explicit default | `str \| None = None` |
| Whitespace-only titles accepted | `min_length` counts spaces | strip in a `field_validator` |

## 15. Exercises

1. Build `BookCreate` with every constraint in section 5, then send the
   five-error payload from section 4 and confirm you get five `detail` entries.
2. Add a real ISBN-13 **checksum** validator. A pattern proves the shape;
   the checksum proves the number. Note how much better the error message can be.
3. Add `discount_price` with the cross-field rule, then try to express the same
   rule with `Field(...)` alone and write down why you cannot.
4. Add `contributors: list[Author]`, send a bad email in the third one, and read
   the `loc`. Draw what a UI would do with that path.
5. Set `extra="forbid"` and send `{"titel": "x"}`. Then decide whether you would
   ship that for a public API, and why.
6. Normalise ISBNs in `mode="before"`: strip spaces and hyphens, upper-case, then
   re-format canonically. Confirm `978 1 4919 4600 8` is accepted and stored
   consistently.
7. Add `alias="bookTitle"` with `populate_by_name=True` so both spellings work,
   and check what `/docs` now advertises.
8. Send `{"price": 4199.0}` and `{"price": "4199.00"}` and print
   `repr(book.price)` for both. That difference is why money is a string.

## 16. What's next

**[Day 05 — Response Models and Status Codes →](../05_response_models_and_status_codes/)**
Today you controlled what comes *in*. Tomorrow you control what goes *out* —
response models as a leak-proof filter, inheritance to kill the duplication from
section 9, and the status code each operation owes its client.
