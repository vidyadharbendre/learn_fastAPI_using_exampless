# Learn FastAPI in 21 Days — by Example

> **One repository, one product.** Every day adds a layer to the same real
> application — **Shelfspace**, an online bookstore API — until it is something
> you would be comfortable putting behind a load balancer.

No `{"hello": "world"}` and no toy snippets that fall apart the moment a second
developer touches them. Each day ships runnable code, a README that explains
*why* the code looks like that, and a table of the mistakes that bite people in
production.

---

## The 21 days

| Day | Folder | What you build | Port |
|-----|--------|----------------|------|
| 01 | [`01_environment_setup_and_first_api`](01_environment_setup_and_first_api/) | Virtualenv, settings, app factory, `/health` | 8001 |
| 02 | [`02_http_methods_and_routing`](02_http_methods_and_routing/) | Full CRUD verbs, status codes, routing rules | 8002 |
| 03 | [`03_path_and_query_parameters`](03_path_and_query_parameters/) | Typed paths, filters, search, `Annotated` | 8003 |
| 04 | [`04_request_bodies_and_pydantic_models`](04_request_bodies_and_pydantic_models/) | Request models, nesting, custom validators | 8004 |
| 05 | [`05_response_models_and_status_codes`](05_response_models_and_status_codes/) | Output contracts, field filtering, 201 + Location | 8005 |
| 06 | [`06_validation_and_error_handling`](06_validation_and_error_handling/) | One error envelope, exception handlers | 8006 |
| 07 | [`07_project_structure_and_routers`](07_project_structure_and_routers/) | `APIRouter`, layered layout, settings per env | 8007 |
| 08 | [`08_dependency_injection`](08_dependency_injection/) | `Depends`, sub-dependencies, yield, overrides | 8008 |
| 09 | [`09_databases_with_sqlalchemy`](09_databases_with_sqlalchemy/) | SQLAlchemy 2.0, sessions, Alembic migrations | 8009 |
| 10 | [`10_crud_and_repository_pattern`](10_crud_and_repository_pattern/) | Repository + service layers, transactions | 8010 |
| 11 | [`11_relationships_and_query_optimization`](11_relationships_and_query_optimization/) | Joins, N+1, eager loading, indexes | 8011 |
| 12 | [`12_rest_api_design_and_pagination`](12_rest_api_design_and_pagination/) | Resource design, pagination, versioning, ETags | 8012 |
| 13 | [`13_middleware_cors_and_rate_limiting`](13_middleware_cors_and_rate_limiting/) | Request lifecycle, CORS, request IDs, throttling | 8013 |
| 14 | [`14_async_and_concurrency`](14_async_and_concurrency/) | `async def` vs `def`, the event loop, `gather` | 8014 |
| 15 | [`15_authentication_with_jwt`](15_authentication_with_jwt/) | Password hashing, JWT access + refresh tokens | 8015 |
| 16 | [`16_authorization_roles_and_scopes`](16_authorization_roles_and_scopes/) | Roles, scopes, ownership, API keys | 8016 |
| 17 | [`17_testing_with_pytest`](17_testing_with_pytest/) | Fixtures, DB isolation, async tests, coverage | 8017 |
| 18 | [`18_background_tasks_and_workers`](18_background_tasks_and_workers/) | `BackgroundTasks`, queues, retries, idempotency | 8018 |
| 19 | [`19_file_uploads_and_streaming`](19_file_uploads_and_streaming/) | Uploads, validation, streaming responses | 8019 |
| 20 | [`20_websockets_and_realtime`](20_websockets_and_realtime/) | WebSockets, connection manager, SSE | 8020 |
| 21 | [`21_observability_docker_and_deployment`](21_observability_docker_and_deployment/) | Structured logs, metrics, Docker, CI, Gunicorn | 8021 |

Each day runs on its own port, so you can leave yesterday's server up and
compare behaviour side by side.

---

## Setup (once)

```bash
git clone git@github.com:vidyadharbendre/learn_fastAPI_using_exampless.git
cd learn_fastAPI_using_exampless

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Verify it worked:

```bash
cd 01_environment_setup_and_first_api
python run.py
```

Then open <http://127.0.0.1:8001/docs>.

## Running the tests

```bash
source .venv/bin/activate
pytest                     # everything
pytest -m day01            # one day
pytest -m structure        # the repo's own layout rules
pytest --cov=. --cov-report=term-missing
```

Tests live in [`tests/`](tests/) and call each day's app **in-process** through
`TestClient` — no server, no port, no network. The whole suite runs in under a
second.

## How each day is laid out

```
NN_topic_name/
├── README.md          the lesson — read this first
├── run.py             `python run.py` starts the server on that day's port
├── .env.example       every setting the day understands
└── shelfspace/        the application package for that day
```

Every README follows the same shape: **why it matters → what you build → run it
→ try it with `curl` → the concepts → best practices → common mistakes →
exercises → what's next.**

## How to use this course

1. **Read section 1 and 2** of the day's README before looking at the code.
2. **Run it** and work through the *Try it* `curl` block. Read the responses,
   including the error bodies — especially the error bodies.
3. **Read the code** alongside sections 5 onwards.
4. **Do at least one exercise.** The exercises are where the learning happens;
   the reading only makes it feel like it did.
5. **Break it on purpose.** Send the wrong type, drop a required field, ask for
   page 900. Understanding the failure modes is the job.

## Conventions used throughout

| Convention | Reason |
|---|---|
| Money crosses the wire as a **string** | JSON floats lose precision |
| Timestamps are UTC **with an explicit offset** | naive timestamps are ambiguous |
| Collections return an **envelope**, never a bare array | leaves room for pagination without a breaking change |
| One **error envelope** for every failure | clients write one parser, not five |
| Settings come from the **environment**, validated at startup | a container that refuses to start beats one that misbehaves |
| Application **factory** (`create_app`) | tests need an app configured differently |
| Interactive docs **disabled in production** | they publish your whole attack surface |

## Requirements

Python **3.11+**. Pinned dependency versions are in
[`requirements.txt`](requirements.txt) — the versions this course was written
and tested against.

## License

[MIT](LICENSE)
