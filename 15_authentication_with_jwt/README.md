# Day 15 — Authentication with JWT

> **Goal:** prove who is calling — password hashing done properly, short-lived
> access tokens, refresh tokens you can revoke, and one dependency that turns a
> header into a `User`.
> **Time:** ~3 hours · **Port:** 8015 · **Builds on:** Day 14

> **Code status:** the README is the spec. Build `shelfspace/` yourself from
> sections 5 onwards before reaching for a reference implementation.

---

## 1. Why this matters

> **Authentication is the one feature where "it works" and "it is correct" are
> completely unrelated.**

A login endpoint that returns a token works. It can simultaneously store
passwords with SHA-256, sign tokens with `"secret"`, never expire them, and
report "user not found" so an attacker can enumerate your accounts. Every one of
those passes a test suite.

Today is the careful version. The FastAPI part is small; the security decisions
are the lesson, and most of them cost nothing at the time and everything later.

## 2. What you will build

```
15_authentication_with_jwt/
├── run.py
└── shelfspace/
    ├── auth/
    │   ├── passwords.py     hash + verify, off the event loop (Day 14)
    │   ├── tokens.py        encode/decode, claims, expiry
    │   ├── service.py       register, login, refresh, logout
    │   └── deps.py          get_current_user, get_optional_user
    ├── db/models.py         User, RefreshToken
    └── api/v1/auth.py       /register /login /refresh /logout /me
```

| Endpoint | Does |
|---|---|
| `POST /auth/register` | create a user, hash the password |
| `POST /auth/login` | verify credentials → access + refresh tokens |
| `POST /auth/refresh` | rotate the refresh token → a new access token |
| `POST /auth/logout` | revoke the refresh token |
| `GET /auth/me` | the protected endpoint that proves it works |

## 3. Run it

```bash
source .venv/bin/activate
cd 15_authentication_with_jwt
export SHELFSPACE_SECRET_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(64))")
alembic upgrade head
python run.py
```

The app **refuses to start in production without a secret key** (section 12).
That is deliberate.

## 4. Try it — learn by doing

```bash
API=http://127.0.0.1:8015/api/v1
JSON='Content-Type: application/json'

# --- register, then log in ---
curl -sX POST $API/auth/register -H "$JSON" \
  -d '{"email":"alice@example.com","password":"correct-horse-battery-staple"}' | python -m json.tool

TOKENS=$(curl -sX POST $API/auth/login -H "$JSON" \
  -d '{"email":"alice@example.com","password":"correct-horse-battery-staple"}')
ACCESS=$(echo $TOKENS  | python -c "import json,sys; print(json.load(sys.stdin)['access_token'])")
REFRESH=$(echo $TOKENS | python -c "import json,sys; print(json.load(sys.stdin)['refresh_token'])")

# --- a token is READABLE by anyone. Never put secrets in it. ---
echo $ACCESS | cut -d. -f2 | base64 -d 2>/dev/null | python -m json.tool

# --- protected endpoints ---
curl -s $API/auth/me                                  | python -m json.tool   # 401
curl -s $API/auth/me -H "Authorization: Bearer $ACCESS" | python -m json.tool # 200

# --- every way a token can be rejected, each with its own code ---
curl -s $API/auth/me -H "Authorization: $ACCESS"           | python -m json.tool  # no "Bearer"
curl -s $API/auth/me -H "Authorization: Bearer garbage"    | python -m json.tool  # malformed
curl -s $API/auth/me -H "Authorization: Bearer ${ACCESS}x" | python -m json.tool  # bad signature

# --- tamper with the payload and re-encode: the signature catches it ---
python - <<'PY'
import base64, json, os
h, p, s = os.environ["ACCESS"].split(".")
payload = json.loads(base64.urlsafe_b64decode(p + "=="))
payload["sub"] = "1"                       # try to become another user
p2 = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
print(f"{h}.{p2}.{s}")
PY
# use that token → 401 invalid_token

# --- wrong password and unknown user look IDENTICAL (and take the same time) ---
curl -s -o /dev/null -w '%{http_code} %{time_total}s  wrong password\n' -X POST $API/auth/login \
  -H "$JSON" -d '{"email":"alice@example.com","password":"nope"}'
curl -s -o /dev/null -w '%{http_code} %{time_total}s  no such user\n'   -X POST $API/auth/login \
  -H "$JSON" -d '{"email":"nobody@example.com","password":"nope"}'

# --- expiry ---
SHELFSPACE_ACCESS_TOKEN_MINUTES=0.05 python run.py &     # 3 seconds
# log in, wait 4 seconds, call /auth/me → 401 token_expired

# --- refresh rotates: the OLD refresh token stops working ---
NEW=$(curl -sX POST $API/auth/refresh -H "$JSON" -d "{\"refresh_token\":\"$REFRESH\"}")
curl -sX POST $API/auth/refresh -H "$JSON" -d "{\"refresh_token\":\"$REFRESH\"}" | python -m json.tool  # 401 reused

# --- logout revokes ---
curl -sX POST $API/auth/logout -H "$JSON" -d "{\"refresh_token\":\"$REFRESH\"}"
curl -sX POST $API/auth/refresh -H "$JSON" -d "{\"refresh_token\":\"$REFRESH\"}" | python -m json.tool  # 401

# --- brute force is rate limited (Day 13), by IP AND by account ---
for i in $(seq 1 12); do
  curl -s -o /dev/null -w '%{http_code} ' -X POST $API/auth/login -H "$JSON" \
    -d '{"email":"alice@example.com","password":"wrong"}'
done; echo

# --- and /docs has an Authorize button that works ---
open http://127.0.0.1:8015/docs
```

Decoding the token in command two is the most important thing you will do today.
**A JWT is signed, not encrypted.** Anyone holding it can read every claim.

## 5. Passwords

```python
# auth/passwords.py
from passlib.context import CryptContext
from anyio import to_thread

pwd = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=12)

async def hash_password(plain: str) -> str:
    return await to_thread.run_sync(pwd.hash, plain)

async def verify_password(plain: str, hashed: str) -> bool:
    return await to_thread.run_sync(pwd.verify, plain, hashed)
```

| Rule | Why |
|---|---|
| **bcrypt or argon2, never SHA/MD5** | fast hashes are the point of a GPU cracking rig |
| The salt is inside the hash | passlib handles it; never roll your own |
| ~12 bcrypt rounds | tune so verification takes 50–200 ms on your hardware |
| **Off the event loop** | it is CPU-bound by design (Day 14) |
| `deprecated="auto"` | lets you migrate algorithms later, transparently |
| Never log or return the hash | Day 05's `response_model` already guards this |
| bcrypt truncates at 72 bytes | reject longer passwords, or pre-hash |

**Password policy:** enforce a minimum **length** (12+) rather than character
classes — length is what actually helps, and complexity rules push people towards
`Passw0rd!`. Check against a breached-password list if you can. Never impose a
maximum below ~64 characters; it signals you are not hashing.

## 6. What a JWT actually is

```
eyJhbGciOiJIUzI1NiJ9 . eyJzdWIiOiI0MiIsImV4cCI6MTc… . 4H2j0…
     header                    payload                signature
```

Base64url, dot-separated, **signed not encrypted**. The signature proves the
payload was not modified and that it came from someone holding your key. It
proves nothing about confidentiality.

```python
CLAIMS = {
    "sub": str(user.id),          # subject — WHO. A string, per spec.
    "exp": now + timedelta(minutes=15),   # expiry — REQUIRED
    "iat": now,                   # issued at
    "jti": uuid4().hex,           # unique id — lets you revoke this one token
    "typ": "access",              # access vs refresh — check it on use
    "iss": "shelfspace",          # issuer
    "aud": "shelfspace-api",      # audience
}
```

Put in it: an id, an expiry, a type. Do **not** put in it: passwords, card
numbers, addresses, anything you would not print on a postcard — or anything that
changes often (a role you might revoke, an email the user can edit), because a
token cannot be updated after it is issued.

```python
jwt.encode(claims, settings.secret_key, algorithm="HS256")
jwt.decode(token, settings.secret_key, algorithms=["HS256"],   # a LIST, always
           audience="shelfspace-api", issuer="shelfspace")
```

**The `alg: none` and algorithm-confusion attacks** are why `algorithms=` is a
fixed list you control. Never derive the verification algorithm from the token's
own header.

**HS256 vs RS256:** HS256 is one shared secret — fine when the same service signs
and verifies. RS256 signs with a private key and verifies with a public one — use
it when several services verify tokens they did not issue, so a compromised
verifier cannot mint tokens.

## 7. Access and refresh: the two-token pattern

| | Access | Refresh |
|---|---|---|
| Lifetime | 15 minutes | 7–30 days |
| Sent to | every API call | only `/auth/refresh` |
| Stored | memory (browser) | httpOnly cookie or secure storage |
| Revocable | not really | **yes** — it is in the database |
| If stolen | 15 minutes of damage | until you revoke it |

The trade-off this resolves: a stateless token cannot be revoked, so it must be
short-lived; a short-lived token would force logins every fifteen minutes. The
refresh token, stored server-side, gives you both.

```python
class RefreshToken(Base):
    jti:        Mapped[str] = mapped_column(unique=True, index=True)
    user_id:    Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    expires_at: Mapped[datetime]
    revoked_at: Mapped[datetime | None] = None
    replaced_by: Mapped[str | None] = None        # the rotation chain
```

Store the **`jti`**, not the token string — the token is a bearer credential and
your database is not the place for it.

**Rotation**: every refresh issues a new refresh token and revokes the old one.
Then reuse of an old one is detectable — and it means either a replay or a theft:

```python
if stored.revoked_at is not None:
    revoke_all_for_user(stored.user_id)      # assume compromise; log everyone out
    raise APIError(401, "token_reused", "Please sign in again.")
```

That single rule is what turns "a stolen refresh token works forever" into
"a stolen refresh token gets both parties logged out and you an alert".

## 8. The dependency that turns a header into a user

```python
# auth/deps.py
bearer = HTTPBearer(auto_error=False)        # or OAuth2PasswordBearer for /docs

async def get_current_user(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    session: SessionDep,
) -> User:
    if creds is None:
        raise APIError(401, "unauthenticated", "Authentication required.")
    try:
        claims = decode_token(creds.credentials, expected_type="access")
    except ExpiredSignatureError:
        raise APIError(401, "token_expired", "Access token has expired.")
    except InvalidTokenError:
        raise APIError(401, "invalid_token", "Token is not valid.")

    user = session.get(User, int(claims["sub"]))
    if user is None or not user.is_active:
        raise APIError(401, "invalid_token", "Token is not valid.")
    return user

CurrentUser = Annotated[User, Depends(get_current_user)]
```

Then protecting an endpoint is one parameter:

```python
@router.get("/me", response_model=UserPublic)
async def me(user: CurrentUser) -> User:
    return user
```

Details that matter:

- **Distinguish `token_expired` from `invalid_token`** in the `code` — a client
  must know when to refresh rather than to re-login. (Day 06's split between
  machine code and human message is what makes this safe.)
- **Look the user up.** A valid signature is not enough: the account may be
  deleted, disabled, or banned since the token was issued.
- **`get_optional_user`** for endpoints that behave differently when signed in
  (returning `None` instead of raising) — but never for endpoints that must be
  protected.
- Use `OAuth2PasswordBearer(tokenUrl="auth/login")` if you want the **Authorize**
  button in `/docs`; it expects a form-encoded `username`/`password`, which is
  worth knowing before you debug it for twenty minutes.

## 9. Login, done carefully

```python
async def login(session: Session, email: str, password: str) -> TokenPair:
    user = repo.by_email(email)
    if user is None:
        await verify_password(password, DUMMY_HASH)      # constant-ish time
        raise APIError(401, "invalid_credentials", "Incorrect email or password.")
    if not await verify_password(password, user.password_hash):
        raise APIError(401, "invalid_credentials", "Incorrect email or password.")
    if not user.is_active:
        raise APIError(403, "account_disabled", "This account is disabled.")
    return issue_tokens(session, user)
```

Three rules in nine lines:

1. **One message for both failures.** "No such user" tells an attacker which
   emails are registered — that is free reconnaissance, and for some services it
   is itself a disclosure ("this person has an account here").
2. **Verify a dummy hash when the user is missing.** Otherwise the *timing*
   leaks it: a missing user returns in 1 ms, a wrong password in 100 ms.
3. **Rate limit login hard** (Day 13), by IP **and** by account, or the message
   discipline above is irrelevant — an attacker just tries 10,000 passwords.

Register has the same enumeration problem: `"email already registered"` is a
disclosure. The privacy-preserving pattern is to always return `202` and send an
email that says either "confirm your address" or "someone tried to register with
your address". Decide consciously; many products accept the disclosure for
usability.

## 10. Where the client stores the token

| Storage | XSS | CSRF | Notes |
|---|---|---|---|
| `localStorage` | **exposed** — any script reads it | safe | most common, least safe |
| Memory only | safe-ish | safe | lost on refresh; pair with a cookie refresh |
| httpOnly cookie | **not readable by JS** | needs protection | best for browsers |

For a browser app: refresh token in an `httpOnly; Secure; SameSite=Lax` cookie,
access token in memory. For a mobile app: the platform keychain. For
server-to-server: an API key or client credentials, not a user token.

If you use cookies you must handle CSRF — `SameSite=Lax` covers most of it; add a
double-submit token for state-changing requests if you support older browsers or
cross-site flows.

## 11. Logout, and the revocation problem

A signed token is valid until it expires; there is no "un-sign". So:

- **Logout** = revoke the refresh token (a database row) and have the client
  discard the access token. The access token remains technically valid for its
  remaining minutes. That is the accepted trade-off, and it is why access tokens
  are short.
- **Immediate revocation** requires state: a `jti` denylist in Redis (with a TTL
  equal to the token lifetime), or a `token_version` on the user that is part of
  the claims and checked on each request. Both cost a lookup per request — which
  is exactly the statelessness you were buying. Add it when you need it
  (compromised account, forced logout), not by default.
- **Password change** should revoke every refresh token for that user. So should
  a detected reuse (section 7).

## 12. Secrets and configuration

```python
class Settings(BaseSettings):
    secret_key: SecretStr
    access_token_minutes: int = 15
    refresh_token_days: int = 14
    jwt_algorithm: Literal["HS256", "RS256"] = "HS256"

    @model_validator(mode="after")
    def secret_must_be_strong(self):
        if self.is_production and len(self.secret_key.get_secret_value()) < 32:
            raise ValueError("SECRET_KEY must be at least 32 bytes in production")
        return self
```

- **Generate it properly**: `secrets.token_urlsafe(64)`. Not "changeme", not the
  project name, not a UUID you found.
- **Never commit it.** A key in git history is compromised even after the commit
  that removes it — rotate, do not delete.
- **`SecretStr`** keeps it out of logs and tracebacks (`repr` prints `**********`).
- **Rotation**: verify against a list of keys, sign with the newest. Then rotating
  does not log everyone out.
- **HTTPS only.** A bearer token over HTTP is a password shouted across the room.
  Combine with `HTTPSRedirectMiddleware` / HSTS (Day 13).

## 13. Best practices introduced today

| Practice | Reason |
|---|---|
| bcrypt/argon2, never a fast hash | fast hashing is what GPUs are for |
| Hash and verify off the event loop | it is CPU-bound by design |
| Minimum length over character classes | length is what actually helps |
| Never put secrets in a JWT | it is signed, not encrypted |
| Always set `exp`; keep access tokens short | you cannot revoke a stateless token |
| `algorithms=["HS256"]` as a fixed list | blocks `alg: none` and confusion attacks |
| Verify `iss`/`aud` when you set them | a token from elsewhere should not work here |
| Check the token `typ` | a refresh token must not authorise an API call |
| Two-token pattern with server-side refresh | revocable *and* convenient |
| Store the `jti`, not the token | the token is a bearer credential |
| Rotate refresh tokens; detect reuse | turns theft into a detectable event |
| Look the user up on every request | tokens outlive account changes |
| Distinguish `token_expired` from `invalid_token` | clients need to know whether to refresh |
| Identical message and timing for login failures | prevents account enumeration |
| Rate limit login by IP and by account | the rest is pointless without it |
| httpOnly cookie for refresh, memory for access | limits XSS damage |
| Revoke all tokens on password change | that is what users think it does |
| `SecretStr`, generated with `secrets` | keeps keys out of logs and out of guessing |
| HTTPS everywhere | a bearer token in plaintext is a password in plaintext |

## 14. Common mistakes

| Symptom | Cause | Fix |
|---|---|---|
| Passwords cracked after a leak | SHA-256 / MD5 | bcrypt or argon2 |
| Logins stall the whole API | hashing on the event loop | `to_thread.run_sync` |
| Secrets visible in a token | assumed JWTs are encrypted | they are not — store an id |
| Token never expires | no `exp` claim | always set it |
| Forged token accepted | `verify=False`, or algorithm from the header | fixed `algorithms=` list |
| Refresh token works as an access token | `typ` not checked | check it |
| Cannot log anyone out | pure stateless design | server-side refresh tokens |
| Stolen refresh token used forever | no rotation | rotate and detect reuse |
| Deleted user still authenticated | trusting claims without a lookup | load the user |
| Client cannot tell expiry from forgery | one generic 401 code | distinct `code`s |
| Attacker enumerates accounts | different messages for the two failures | one message |
| Attacker enumerates by timing | early return when the user is missing | verify a dummy hash |
| 10,000 password attempts | no rate limit on login | limit by IP and account |
| XSS stole every session | tokens in `localStorage` | httpOnly cookie for refresh |
| Secret key in the repo | committed `.env` | rotate the key, then fix `.gitignore` |
| Everyone logged out by a key rotation | single-key verification | verify against a key list |
| Token replayed from another service | no `aud`/`iss` check | set and verify them |
| `/docs` Authorize button does nothing | `HTTPBearer` instead of `OAuth2PasswordBearer` | use the OAuth2 scheme |

## 15. Exercises

1. Implement register/login/me with bcrypt and a `CurrentUser` dependency. Then
   decode your own access token and confirm you can read every claim.
2. Try to forge a token: change `sub` and re-encode without the key. Confirm the
   `401` and explain which property of the signature stopped you.
3. Set the access-token lifetime to 3 seconds and drive the full
   expire → refresh → retry cycle from the client side.
4. Implement refresh rotation with reuse detection. Then simulate a theft: use an
   old refresh token and confirm every session for that user is revoked.
5. Measure the timing difference between "wrong password" and "no such user"
   without the dummy-hash fix, then add it and measure again.
6. Add rate limiting to `/auth/login` (Day 13) keyed by IP **and** by email, and
   demonstrate both limits.
7. Move the refresh token into an `httpOnly; Secure; SameSite=Lax` cookie and
   list what changes on the client and what you now owe CSRF-wise.
8. Add `token_version` to `User`, include it in the claims, and use it to log a
   user out of everything on password change. Note the per-request cost.

## 16. What's next

**[Day 16 — Authorization, Roles and Scopes →](../16_authorization_roles_and_scopes/)**
You now know *who* is calling. Tomorrow: what they are allowed to do — roles,
scopes, ownership checks, and the object-level permission bug that appears in
every top-ten vulnerability list.
