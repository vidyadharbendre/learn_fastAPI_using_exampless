# Day 16 — Authorization, Roles and Scopes

> **Goal:** decide what an authenticated caller may do — roles, scopes,
> ownership, and the object-level check that is missing from most APIs you have
> ever used.
> **Time:** ~2.5 hours · **Port:** 8016 · **Builds on:** Day 15

> **Code status:** the README is the spec. Build `shelfspace/` yourself from
> sections 5 onwards before reaching for a reference implementation.

---

## 1. Why this matters

> **Authentication asks "who are you?". Authorization asks "may you?". Getting
> the first right and skipping the second is the most common serious API
> vulnerability there is.**

```python
@router.get("/orders/{order_id}")
async def get_order(order_id: int, user: CurrentUser):
    return repo.get(order_id)          # ← whose order? Nobody asked.
```

That endpoint requires a valid token and then hands any authenticated user any
order. Change the id in the URL, read someone else's address and card summary.
It is called **broken object-level authorization** (BOLA/IDOR), it sits at the
top of the OWASP API list year after year, and the fix is one `if`.

Today is about making that `if` systematic rather than remembered.

## 2. What you will build

```
16_authorization_roles_and_scopes/
├── run.py
└── shelfspace/
    ├── auth/
    │   ├── roles.py        Role enum + role→permissions map
    │   ├── scopes.py       token scopes, and how they intersect with roles
    │   ├── policy.py       can_view / can_edit / can_delete — the rules
    │   └── deps.py         require_role, require_scope, get_owned_*
    ├── api/v1/…            endpoints that are protected by construction
    └── db/models.py        User(role), ApiKey(scopes), Order(user_id)
```

## 3. Run it

```bash
source .venv/bin/activate
cd 16_authorization_roles_and_scopes
alembic upgrade head && python -m shelfspace.seed   # alice(member), bob(member), carol(admin)
python run.py
```

## 4. Try it — learn by doing

```bash
API=http://127.0.0.1:8016/api/v1
login() { curl -sX POST $API/auth/login -H 'Content-Type: application/json' \
  -d "{\"email\":\"$1\",\"password\":\"password123\"}" \
  | python -c "import json,sys; print(json.load(sys.stdin)['access_token'])"; }

ALICE=$(login alice@example.com)    # member
BOB=$(login bob@example.com)        # member
CAROL=$(login carol@example.com)    # admin

# --- ownership: Alice's order is Alice's ---
curl -s $API/orders/1 -H "Authorization: Bearer $ALICE" | python -m json.tool   # 200
curl -s $API/orders/1 -H "Authorization: Bearer $BOB"   | python -m json.tool   # 404, not 403
curl -s $API/orders/1 -H "Authorization: Bearer $CAROL" | python -m json.tool   # 200 (admin)

# --- roles ---
curl -s -o /dev/null -w 'member deletes book: %{http_code}\n' -X DELETE $API/books/1 \
  -H "Authorization: Bearer $ALICE"                                            # 403
curl -s -o /dev/null -w 'admin  deletes book: %{http_code}\n' -X DELETE $API/books/1 \
  -H "Authorization: Bearer $CAROL"                                            # 204

# --- 401 vs 403: two different answers ---
curl -s -o /dev/null -w 'no token   : %{http_code}\n' -X DELETE $API/books/2
curl -s -o /dev/null -w 'wrong role : %{http_code}\n' -X DELETE $API/books/2 \
  -H "Authorization: Bearer $ALICE"

# --- collections are filtered, not just guarded ---
curl -s $API/orders -H "Authorization: Bearer $ALICE" | python -c "
import json,sys; print('alice sees', len(json.load(sys.stdin)['data']))"
curl -s $API/orders -H "Authorization: Bearer $CAROL" | python -c "
import json,sys; print('carol sees', len(json.load(sys.stdin)['data']))"

# --- mass assignment: a member cannot promote themselves ---
curl -sX PATCH $API/users/me -H "Authorization: Bearer $ALICE" \
  -H 'Content-Type: application/json' -d '{"name":"Alice A.","role":"admin"}' | python -m json.tool
curl -s $API/auth/me -H "Authorization: Bearer $ALICE" | python -c "
import json,sys; print('role is still', json.load(sys.stdin)['role'])"

# --- scopes: a read-only API key cannot write ---
KEY=$(curl -sX POST $API/apikeys -H "Authorization: Bearer $CAROL" \
  -H 'Content-Type: application/json' -d '{"name":"reporting","scopes":["books:read"]}' \
  | python -c "import json,sys; print(json.load(sys.stdin)['key'])")
curl -s -o /dev/null -w 'key read : %{http_code}\n' $API/books -H "X-API-Key: $KEY"
curl -s -o /dev/null -w 'key write: %{http_code}\n' -X POST $API/books -H "X-API-Key: $KEY" \
  -H 'Content-Type: application/json' -d '{}'

# --- and a 403 says WHAT is required, without leaking anything else ---
curl -s -X DELETE $API/books/3 -H "Authorization: Bearer $ALICE" | python -m json.tool
```

The `404` in line two is deliberate and section 8 explains why.

## 5. Roles: the coarse layer

```python
class Role(str, Enum):
    guest    = "guest"
    member   = "member"
    librarian = "librarian"
    admin    = "admin"

PERMISSIONS: dict[Role, set[str]] = {
    Role.guest:     {"books:read"},
    Role.member:    {"books:read", "orders:read:own", "orders:create", "reviews:write"},
    Role.librarian: {"books:read", "books:write", "orders:read", "loans:manage"},
    Role.admin:     {"*"},
}
```

Two design choices worth stating:

**Map roles to permissions; check permissions, not roles.** `if user.role ==
"admin"` scattered through the codebase is how you end up unable to add a
"support" role that can do *most* admin things. One indirection removes that
whole class of rewrite.

**Avoid deep role hierarchies.** "Librarian inherits member, manager inherits
librarian" reads well and becomes impossible to reason about at level four. A
flat map of role → permission set is boring and always answerable.

Store the role **on the user** (a database column), not only in the token: a role
change must take effect on the next request, not in fifteen minutes (Day 15,
section 6). If you do put it in the token, keep the lifetime short and accept the
lag consciously.

## 6. Scopes: the fine layer

Scopes describe what a **token** may do, which is not the same as what its owner
may do. A read-only API key belonging to an admin should still not delete books.

```python
class TokenData(BaseModel):
    sub: str
    scopes: list[str] = []
```

> **Effective permission = user's permissions ∩ token's scopes.**

That intersection is the entire model, and it is what makes "give this reporting
script access to my data, read-only" possible without creating a second account.

FastAPI has first-class support via `SecurityScopes`:

```python
oauth2 = OAuth2PasswordBearer(tokenUrl="auth/login",
                              scopes={"books:read": "Read the catalogue",
                                      "books:write": "Create and edit books"})

async def get_current_user(security_scopes: SecurityScopes,
                           token: Annotated[str, Depends(oauth2)]) -> User:
    claims = decode_token(token)
    for scope in security_scopes.scopes:
        if scope not in claims.get("scopes", []):
            raise APIError(403, "insufficient_scope",
                           f"Requires scope {scope!r}.",
                           details={"required": security_scopes.scopes})
    ...

@router.post("/books")
async def create(user: Annotated[User, Security(get_current_user, scopes=["books:write"])]):
```

`Security` is `Depends` plus scope propagation — and the scopes appear in
`/openapi.json`, so the docs state what each endpoint needs.

Name scopes `resource:action` (`books:read`, `orders:write`), keep the list short,
and remember: **the scope is a ceiling, never a grant.** A token with
`books:write` still cannot write if its owner is a `member`.

## 7. Ownership: the check everyone forgets

```python
# auth/policy.py
def can_view_order(user: User, order: Order) -> bool:
    return order.user_id == user.id or has_permission(user, "orders:read")

def can_edit_review(user: User, review: Review) -> bool:
    return review.author_id == user.id or has_permission(user, "reviews:moderate")
```

Then make it impossible to forget by putting the check in the **loader**, not the
handler:

```python
async def get_owned_order(order_id: int, user: CurrentUser,
                          session: SessionDep) -> Order:
    order = session.get(Order, order_id)
    if order is None or not can_view_order(user, order):
        raise NotFound("order", order_id)      # 404 either way — see section 8
    return order

OwnedOrder = Annotated[Order, Depends(get_owned_order)]

@router.get("/orders/{order_id}", response_model=OrderPublic)
async def read_order(order: OwnedOrder) -> Order:
    return order                                # nothing left to forget
```

The handler now *cannot* return an order the caller may not see, because it never
gets one. That is the structural fix; a remembered `if` in every handler is not.

**Collections need the same treatment, as a filter:**

```python
stmt = select(Order)
if not has_permission(user, "orders:read"):
    stmt = stmt.where(Order.user_id == user.id)     # scoped at the query
```

Filtering in the query, not after fetching, is both correct and fast — and it
means pagination counts are right for that user.

## 8. `401` vs `403` vs `404`

| Code | Means | Client should |
|---|---|---|
| `401 Unauthorized` | not authenticated (or the token is bad/expired) | log in or refresh |
| `403 Forbidden` | authenticated, not permitted | stop; ask for access |
| `404 Not Found` | it does not exist — **or you may not know that it does** | stop |

The `403`-vs-`404` question is a genuine trade-off:

- **`403` for a resource that exists** tells the caller it exists. Iterating ids
  then maps your entire database: "these 4,000 order ids are real".
- **`404` for anything you cannot see** leaks nothing, at the cost of a slightly
  confusing experience for a legitimate user who lost access.

The usual rule: **`404` for objects a caller has no relationship with; `403` when
the caller can legitimately know it exists but lacks the specific right** — e.g.
a member trying to delete a book from the public catalogue. Whichever you choose,
be consistent and document it, and never leak the object's contents in the error.

Also: a `403` should say what is **required** (`"Requires role 'admin'"`), not
what you are (`"You are a member"`). One helps a developer fix their integration;
the other invites probing.

## 9. Where the check belongs

| Layer | Good for | Beware |
|---|---|---|
| **Router dependency** (`dependencies=[...]`) | role/scope gates for a whole router | cannot see the object |
| **Loader dependency** | object-level ownership | the pattern to prefer |
| **Service** | rules involving several objects | must not need HTTP |
| **Query filter** | collections | the only correct way to scope lists |
| **Response model** | field-level (Day 05) | hide, do not just omit in the UI |

Put the coarse gate at the router (Day 08: a new route is protected by default),
and the object-level check in a loader dependency. That combination is what makes
authorization structural instead of remembered.

**Field-level authorization** matters too: an admin listing users may see emails;
a member may not. Two response models is the clean answer:

```python
model = UserAdminView if has_permission(user, "users:read") else UserPublic
```

Never solve this by returning everything and hiding it in the frontend. The API
is the boundary.

## 10. Mass assignment, again

Day 04 separated input and output models. Authorization is why it matters most:

```python
class UserSelfUpdate(BaseModel):     # what a member may change
    name: str | None = None
    email: EmailStr | None = None

class UserAdminUpdate(UserSelfUpdate):   # what an admin may change
    role: Role | None = None
    is_active: bool | None = None
```

Pick the model by the caller's permission, not by the caller's payload. A single
`UserUpdate` with `role` in it means `{"role": "admin"}` is one forgotten check
away from working — and that check will be forgotten exactly once.

## 11. Testing authorization

Permission bugs are invisible to happy-path tests, so test the matrix
deliberately:

```python
@pytest.mark.parametrize("actor,order_owner,expected", [
    ("alice", "alice", 200),
    ("alice", "bob",   404),
    ("carol", "bob",   200),      # admin
    (None,    "alice", 401),
])
def test_order_access(client, actor, order_owner, expected): ...
```

Then add the test that catches the *systemic* failure — an endpoint added later
with no protection at all:

```python
def test_every_route_is_protected(app):
    """Any new route must be authenticated unless it is explicitly public."""
    PUBLIC = {"/health", "/api/v1/", "/api/v1/auth/login", "/api/v1/auth/register"}
    for route in app.routes:
        if route.path in PUBLIC or not hasattr(route, "dependant"):
            continue
        assert requires_auth(route), f"{route.path} has no auth dependency"
```

That test is worth more than any number of individual permission tests, because
it fails on the day someone adds an unprotected endpoint — which is how this
class of bug actually ships.

## 12. Best practices introduced today

| Practice | Reason |
|---|---|
| Authenticate, then **authorize** separately | a valid token is not a permission |
| Map roles → permissions; check permissions | new roles stop requiring a rewrite |
| Keep the role hierarchy flat | deep inheritance becomes unanswerable |
| Store the role in the database, not only the token | revocation must be immediate |
| Effective permission = user ∩ token scopes | scopes are a ceiling, never a grant |
| Name scopes `resource:action` | predictable and greppable |
| Object-level checks in a **loader dependency** | the handler cannot forget |
| Filter collections **in the query** | correct counts, correct pagination, faster |
| Deny by default; make public routes explicit | new endpoints start protected |
| `401` unauthenticated, `403` unpermitted | clients need to know which to fix |
| `404` for objects you may not know exist | prevents id enumeration |
| A `403` names what is required, not who you are | helps integrators, not probers |
| Separate self-update and admin-update models | mass assignment is a privilege escalation |
| Field-level filtering via response models | the API is the boundary, not the UI |
| Parametrised permission matrix tests | the only way this stays correct |
| A test that every route has auth | catches the endpoint added next Tuesday |
| Log authorization failures with actor and target | this is your intrusion signal |

## 13. Common mistakes

| Symptom | Cause | Fix |
|---|---|---|
| Any user reads any order | no ownership check (BOLA/IDOR) | loader dependency |
| List endpoint leaks everyone's rows | filtered after fetching, or not at all | filter in the query |
| Pagination totals wrong per user | count not filtered | scope the count too |
| Attacker maps your ids | `403` on existing objects | `404` where there is no relationship |
| `403` returned when not logged in | conflating the two | `401` for missing auth |
| Member promoted themselves | one update model with `role` | separate models |
| New endpoint unprotected | per-endpoint guards | router-level default + the route test |
| Role change takes 15 minutes | role read from the token | read from the database |
| Read-only key can write | scopes ignored | intersect user and token scopes |
| Scope grants more than the role | treated a scope as a grant | it is a ceiling |
| "Support" role needs a rewrite | `if role == "admin"` everywhere | permission map |
| Nobody can reason about permissions | deep role inheritance | flat map |
| Sensitive fields visible to members | one response model | two views |
| Frontend hides it, API returns it | authorization in the UI | enforce server-side |
| Cannot investigate an incident | authorization failures unlogged | log actor, action, target |
| Admin endpoints found by scanning | relying on obscurity | authorize; do not hide |

## 14. Exercises

1. Implement the `Role` enum and permission map, then convert every
   `if user.role == …` in your code to `has_permission(user, …)`.
2. Add `get_owned_order` as a loader dependency and confirm Bob gets a `404` for
   Alice's order while Carol gets `200`.
3. Filter `GET /orders` in the query by ownership, then verify the `total` in the
   pagination envelope is per-user correct.
4. Write the parametrised access matrix from section 11 for orders and reviews.
5. Add the "every route is protected" test, then add a new unprotected endpoint
   and watch it fail.
6. Implement API keys with scopes and demonstrate that an admin's read-only key
   cannot write.
7. Split `UserUpdate` into self and admin variants, then try
   `{"role": "admin"}` as a member and confirm it is rejected — and that the
   rejection is a `422`, not a silent drop, if you chose `extra="forbid"`.
8. Log every authorization failure with actor, action and target, then write the
   query you would run to spot someone enumerating order ids.

## 15. What's next

**[Day 17 — Testing with Pytest →](../17_testing_with_pytest/)**
You have written seven kinds of rule that must never regress. Tomorrow: fixtures,
per-test database isolation, factories, async tests, `dependency_overrides` in
anger, coverage that means something — and a suite fast enough to run on every
save.
