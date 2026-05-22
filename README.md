# Tenant Billing Service

A minimal invoice and payment service with signed webhooks. Single-currency (USD), integer cents throughout, no floats anywhere in the money path. Built as a take-home assignment.

**Primary design doc:** see [DESIGN.md](./DESIGN.md) — data model, state machine, payment correctness, failure modes, webhooks, and trade-offs are documented there.
**AI usage disclosure:** see [AI_USAGE.md](./AI_USAGE.md).

---

## Demo Video

**[https://www.loom.com/share/c360553f534a48cd83eb1c76503e401f]**

The video walks through architecture, a live demo of payment flows, the invoice state machine, and a failure-mode walkthrough (PSP timeout handling).

---

## Tech Stack

| Layer | Choice | Why |
|---|---|---|
| Language | Python 3.12 | Async-native, mature ecosystem |
| Web framework | FastAPI + Uvicorn | Async, Pydantic-first, automatic OpenAPI |
| Database | PostgreSQL 16 | `SELECT FOR UPDATE`, partial unique indexes, JSONB |
| ORM | SQLAlchemy 2 (async) + Alembic | Explicit, mature, autogenerate migrations |
| HTTP client | httpx | Async, configurable timeouts |
| Background worker | In-process asyncio task | Outbox is in Postgres; durable without a broker |
| Tests | pytest + pytest-asyncio | Real DB + real HTTP against the live stack |

Redis is provisioned in compose as a placeholder for a future external worker queue — not actively used by the current code path.

---

## Quick Start

Prerequisites: Docker + Docker Compose.

```bash
docker compose up -d
```

This brings up four containers:

| Service | Port | Role |
|---|---|---|
| `app` | 8000 | The FastAPI service (this codebase) |
| `mock_psp` | 8001 | Mock payment processor (deterministic by `card_token`) |
| `db` | 5432 | PostgreSQL 16 |
| `redis` | — | Reserved for a future external worker queue |

On first boot, `app` automatically runs `alembic upgrade head` before starting Uvicorn — schema and indexes are created without manual steps.

Verify the stack is healthy:

```bash
curl -s http://localhost:8000/health
# {"status":"ok","app":"tenant-billing-service"}

curl -s http://localhost:8000/health/db
# {"status":"ok","database":"reachable"}

curl -s http://localhost:8001/health
# {"status":"ok","service":"mock_psp"}
```

OpenAPI docs are auto-generated at:
- Swagger UI: <http://localhost:8000/docs>
- ReDoc: <http://localhost:8000/redoc>
- Raw JSON: <http://localhost:8000/openapi.json>

---

## Bootstrap an API Key

There is no public sign-up endpoint by design (out of scope per the assignment; see DESIGN.md §6). To create a business and its first API key for testing:

### Step 1 — create a business

```bash
docker compose exec db psql -U invoice_user -d invoice_db -c \
  "INSERT INTO businesses (name) VALUES ('Test Co') RETURNING id;"
```

Copy the returned UUID. You'll need it in step 3.

### Step 2 — generate an API key

```bash
docker compose run --rm app python3 -c "
from app.utils.api_keys import generate_api_key
k = generate_api_key()
print('PLAINTEXT:', k.plaintext)
print('HASH:    ', k.key_hash)
print('PREFIX:  ', k.key_prefix)
"
```

Save the **plaintext** value — it's the API key you'll send in requests. The hash is what gets stored in the DB; the plaintext is shown only at creation time, never retrievable afterward.

### Step 3 — insert the key

```bash
docker compose exec db psql -U invoice_user -d invoice_db -c \
  "INSERT INTO api_keys (business_id, key_hash, key_prefix)
   VALUES ('<business-id-from-step-1>',
           '<hash-from-step-2>',
           '<prefix-from-step-2>');"
```

### Step 4 — export the key for use in curl

```bash
export KEY="sk_live_..."
```

---

## Curl Examples

The four flows that exercise the system end to end. Spec required 3–4 examples; these are the most useful set.

### 1. Create a customer

```bash
curl -i -X POST http://localhost:8000/customers \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{"name":"Alice Example","email":"alice@example.com"}'
```

Response (`201 Created`):

```json
{
  "id": "bb0f7559-1925-4a08-b4c2-bff67ccd1a85",
  "name": "Alice Example",
  "email": "alice@example.com",
  "created_at": "2026-05-22T18:02:24.582594Z",
  "updated_at": "2026-05-22T18:02:24.582594Z"
}
```

Notes:
- Email is unique **per business**, not globally — another tenant can have a customer with the same email.
- Duplicate email within the same business returns `409 Conflict`.

### 2. Create and finalize an invoice

```bash
export CUSTOMER_ID="<id-from-step-1>"

# Create — server computes total from line items; client cannot supply it
curl -i -X POST http://localhost:8000/invoices \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d "{
    \"customer_id\": \"$CUSTOMER_ID\",
    \"due_date\": \"2026-06-30\",
    \"line_items\": [
      {\"description\":\"Design work\",\"quantity\":10,\"unit_amount_cents\":15000},
      {\"description\":\"Hosting\",\"quantity\":1,\"unit_amount_cents\":5000}
    ]
  }"
```

Response (`201 Created`) — note `state: "draft"`, `total_cents: 155000`, line items with computed `amount_cents`:

```json
{
  "id": "e577d062-b4eb-4eae-8331-cee8f381ee02",
  "customer_id": "bb0f7559-1925-4a08-b4c2-bff67ccd1a85",
  "state": "draft",
  "total_cents": 155000,
  "due_date": "2026-06-30",
  "line_items": [
    {"id": "...", "description": "Design work", "quantity": 10,
     "unit_amount_cents": 15000, "amount_cents": 150000},
    {"id": "...", "description": "Hosting", "quantity": 1,
     "unit_amount_cents": 5000, "amount_cents": 5000}
  ],
  "created_at": "...",
  "updated_at": "..."
}
```

Finalize (draft → open). Invoices in `draft` cannot be paid:

```bash
export INV_ID="<id-from-above>"

curl -s -X POST http://localhost:8000/invoices/$INV_ID/finalize \
  -H "Authorization: Bearer $KEY"
```

Returns `200 OK` with `state: "open"`.

### 3. Pay an invoice (success)

```bash
curl -i -X POST http://localhost:8000/invoices/$INV_ID/pay \
  -H "Authorization: Bearer $KEY" \
  -H "Idempotency-Key: $(uuidgen)" \
  -H "Content-Type: application/json" \
  -d '{"card_token":"tok_success"}'
```

Response (`200 OK`):

```json
{
  "id": "8feab681-b197-4e42-886f-267e85ee5587",
  "invoice_id": "e577d062-...",
  "status": "succeeded",
  "amount_cents": 155000,
  "card_token": "tok_success",
  "psp_ref": "ca6caf27-571e-4286-951a-650ef5da488b",
  "failure_code": null,
  "idempotency_key": "...",
  "created_at": "...",
  "updated_at": "..."
}
```

Invoice state is now `paid`. A second `POST /pay` on the same invoice will return `422` (invoice in terminal state).

### 4. Pay an invoice (failure)

```bash
curl -i -X POST http://localhost:8000/invoices/$INV_ID/pay \
  -H "Authorization: Bearer $KEY" \
  -H "Idempotency-Key: $(uuidgen)" \
  -H "Content-Type: application/json" \
  -d '{"card_token":"tok_card_declined"}'
```

Response (`402 Payment Required`):

```json
{
  "id": "...",
  "invoice_id": "...",
  "status": "failed",
  "failure_code": "card_declined",
  "psp_ref": null,
  "...": "..."
}
```

Invoice state stays `open` — the customer can retry with a different card.

### Other PSP outcomes (from the mock PSP)

| `card_token` | HTTP | `attempt.status` | `failure_code` | Invoice state |
|---|---|---|---|---|
| `tok_success` | 200 | succeeded | — | paid |
| `tok_card_declined` | 402 | failed | card_declined | open |
| `tok_insufficient_funds` | 402 | failed | insufficient_funds | open |
| `tok_network_error` | 202 | pending | psp_unavailable | open |
| `tok_timeout` | 202 (after ~12s) | pending | psp_timeout | open |

The pending outcomes are deliberate — when we don't know whether the PSP charged the customer, we record a `pending` attempt and return `202 Accepted` instead of pretending we know the outcome. See DESIGN.md §3 for the full reasoning.

---

## Idempotency

`POST /invoices/{id}/pay` requires an `Idempotency-Key` header. Missing it returns `422`.

The key is scoped to one invoice — the same key on different invoices creates separate attempts.

**Retry semantics:**

| Scenario | Outcome |
|---|---|
| Same key, same body | Returns the cached attempt (no second PSP call) — verified by identical `id` field |
| Same key, **different** body | `409 Conflict` — `"Idempotency key reused with different request body"` |
| New key | New attempt is created and processed |

A SHA-256 hash of the canonical request body is stored on the attempt for body-mismatch detection. Silently serving the cached result for a different body would be a correctness bug.

---

## Webhooks

### Register an endpoint

```bash
curl -X POST http://localhost:8000/webhook_endpoints \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{"url":"https://webhook.site/<your-unique-id>"}'
```

Response includes a `signing_secret` — **shown once**. Subsequent GETs do not include it. If you lose it, deactivate and create a new endpoint.

### Events emitted

| Event | When |
|---|---|
| `invoice.created` | On `POST /invoices` |
| `invoice.paid` | When a `POST /pay` returns `succeeded` from the PSP |
| `invoice.payment_failed` | When a `POST /pay` returns a business failure (declined, insufficient funds) |

Pending payments (PSP timeout / network error) **do not** emit webhooks — only confirmed outcomes do.

### Signature verification (receiver side)

Each delivery includes:

```
X-Webhook-Timestamp: 1779415532
X-Webhook-Signature: t=1779415532,v1=a5bf58d8ab303baa...
```

To verify, compute `HMAC-SHA256(signing_secret, f"{timestamp}.{body}")` and constant-time-compare to the `v1` hex value. Reject signatures older than ~5 minutes to prevent replay.

A reference verifier is in `app/utils/signing.py` (`verify_signature`).

### Retry policy

Webhook delivery is decoupled from the API response path via an outbox table (see DESIGN.md §4). On any non-2xx, connection error, or timeout, the delivery is retried with exponential backoff:

```
attempt 1: immediate
attempt 2: +1s
attempt 3: +5s
attempt 4: +25s
attempt 5: +125s
  → give up (status='exhausted')
```

5 attempts, ~155s total budget. Reconciliation for missed events: the underlying resource state is the source of truth (`GET /invoices/{id}`).

### List registered endpoints

```bash
curl -s http://localhost:8000/webhook_endpoints \
  -H "Authorization: Bearer $KEY"
# Returns paginated list. signing_secret is NEVER included in the response.
```

### Deactivate an endpoint

```bash
curl -X DELETE http://localhost:8000/webhook_endpoints/<endpoint-id> \
  -H "Authorization: Bearer $KEY"
# 204 No Content. Soft-delete (sets is_active=false); existing outbox rows still deliver.
```

---

## Testing

Three required tests per the assignment spec, all run against the live Docker stack:

```bash
docker compose up -d
export KEY="sk_live_..."   # the dev API key set up via the Bootstrap section above
pytest tests/ -v -s
```

Expected: `6 passed in ~13s`. The timeout test takes ~12s on its own — that's the point (PSP sleeps 30s, our timeout cuts it at 12).

### Test coverage

| File | Tests | What it proves |
|---|---|---|
| `tests/test_concurrency.py` | 1 | Fires 5 simultaneous `POST /pay` requests. Exactly one succeeds, others rejected, no double-charges, final invoice state is `paid`. Exercises `SELECT FOR UPDATE` + the partial unique index on pending attempts. |
| `tests/test_idempotency.py` | 3 | Same key + same body returns the cached attempt id (no second PSP call). Same key + different body returns 409. Missing `Idempotency-Key` returns 422. |
| `tests/test_psp_failures.py` | 2 | `tok_network_error` → 202 with `failure_code=psp_unavailable`, invoice stays open. `tok_timeout` → 202 within ~12s (NOT 30s), invoice stays open. |

### Why tests run against the live stack, not SQLite

`SELECT FOR UPDATE` is a no-op in SQLite — it has no row-level locking. The concurrency test would pass trivially even if the lock were removed. Real correctness requires real Postgres, so the suite hits `localhost:8000` and exercises the actual transactional behavior end to end. Documented in `tests/conftest.py`.

---

## Project Layout

```
tenant-billing-service/
├── README.md
├── DESIGN.md                        ← primary design doc (graded)
├── AI_USAGE.md                      ← AI tool usage disclosure
├── docker-compose.yml               ← brings up app + db + redis + mock_psp
├── Dockerfile                       ← invoice service image
├── Dockerfile.mock_psp              ← mock PSP image (separate process)
├── requirements.txt
├── pytest.ini                       ← asyncio_mode = auto
├── alembic.ini
├── alembic/
│   ├── env.py                       ← reads DATABASE_URL_SYNC from settings
│   ├── script.py.mako
│   └── versions/
│       ├── e9e0b3d14d71_initial_schema.py
│       └── ec465f54d10f_add_partial_unique_index_on_pending_*.py
├── app/
│   ├── __init__.py
│   ├── main.py                      ← FastAPI factory, lifespan, /health
│   ├── config.py                    ← pydantic-settings (env-driven)
│   ├── database.py                  ← async engine, session factory, get_db
│   ├── dependencies.py              ← get_current_business (API key auth)
│   ├── exceptions.py                ← domain exceptions (no HTTP knowledge)
│   ├── models/
│   │   ├── business.py              ← Business + ApiKey
│   │   ├── customer.py
│   │   ├── invoice.py               ← Invoice + InvoiceLineItem + InvoiceState enum
│   │   ├── payment.py               ← PaymentAttempt + PaymentStatus enum
│   │   └── webhook.py               ← WebhookEndpoint + WebhookDelivery
│   ├── schemas/                     ← Pydantic request/response shapes
│   │   ├── customer.py
│   │   ├── invoice.py
│   │   ├── payment.py
│   │   └── webhook.py
│   ├── routers/                     ← HTTP layer (thin handlers only)
│   │   ├── customers.py
│   │   ├── invoices.py
│   │   ├── payments.py
│   │   └── webhooks.py
│   ├── services/                    ← business logic (transport-agnostic)
│   │   ├── customer_service.py
│   │   ├── invoice_service.py
│   │   ├── invoice_state.py         ← pure state-machine rules
│   │   ├── payment_service.py       ← locking + idempotency + PSP failure
│   │   ├── psp_client.py            ← httpx wrapper, typed PSP exceptions
│   │   └── webhook_service.py       ← outbox enqueue + endpoint CRUD
│   ├── utils/
│   │   ├── api_keys.py              ← generate / hash / verify (SHA-256)
│   │   └── signing.py               ← HMAC-SHA256 webhook signing
│   └── workers/
│       └── webhook_worker.py        ← async polling loop, retry with backoff
├── mock_psp/
│   ├── __init__.py
│   └── main.py                      ← separate FastAPI app, deterministic by token
└── tests/
    ├── conftest.py                  ← session fixtures, KEY from env
    ├── test_concurrency.py          ← required: N concurrent /pay
    ├── test_idempotency.py          ← required: same key, same response
    └── test_psp_failures.py         ← required: tok_timeout, tok_network_error
```

### Architecture summary

- **`routers/`** parse HTTP, call services, return Pydantic responses. No business logic.
- **`services/`** own all state-machine logic, locking, PSP calls, outbox writes. Testable without HTTP — never import `fastapi`.
- **`models/` and `schemas/`** are strictly separated. ORM models never leave the service layer raw; routers always return `*Read` schemas.
- **Domain exceptions** in services (`InvalidStateTransition`, `IdempotencyConflict`, `NotFoundError`) are translated to `HTTPException` in routers. Service code has no HTTP knowledge.
- **`workers/webhook_worker.py`** runs as a background asyncio task started by FastAPI's `lifespan` — durable via the Postgres outbox, doesn't scale horizontally (one instance only). Documented as a production gap.

---

## Configuration

Environment variables (defaults in `docker-compose.yml`):

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://invoice_user:invoice_pass@db:5432/invoice_db` | Async DB URL for the app |
| `DATABASE_URL_SYNC` | `postgresql+psycopg2://...` | Sync DB URL for Alembic migrations |
| `REDIS_URL` | `redis://redis:6379/0` | Reserved for future worker queue |
| `PSP_URL` | `http://mock_psp:8001/charge` | PSP charge endpoint |
| `PSP_TIMEOUT_SECONDS` | `12` | httpx timeout for PSP calls (must be < tok_timeout's 30s) |
| `SECRET_KEY` | _(no default)_ | App secret (currently unused; reserved for future signing needs) |
| `DEBUG` | `false` | Enables FastAPI debug mode (verbose errors) |

---

## What's NOT here (and why)

Deliberately cut to stay within scope. See DESIGN.md §6 for full reasoning. Headlines:

- **No refunds or partial payments.** Would need a `refunds` table, new state values, dedicated events. Stubbing cheaply would create a worse data model than no model.
- **No subscriptions / recurring billing / proration.** Explicitly out of scope per spec.
- **No multi-currency.** USD only.
- **No tax calculation.**
- **No frontend / UI.**
- **No public sign-up endpoint.** Token-based API auth is the right scope per spec; bootstrap is via psql (documented above).
- **No production rate limiting.** Discussed in DESIGN.md §7.
- **No email sending.** Per spec — logging "would send email" is acceptable.
- **No deployment beyond `docker compose up`.** Per spec — "Brings up app, database, and mock PSP with no further steps."

---

## Useful commands during development

```bash
# Tail logs
docker compose logs -f app

# Restart just the app (e.g. after env var change)
docker compose restart app

# Inspect the DB
docker compose exec db psql -U invoice_user -d invoice_db

# Run migrations manually (normally runs on app startup)
docker compose exec app alembic upgrade head

# Drop everything and start fresh
docker compose down -v   # ⚠ DESTROYS DATA — removes the postgres_data volume

# Run a one-off Python REPL inside the app container
docker compose run --rm app python3

# Generate a new migration after model changes
docker compose exec app alembic revision --autogenerate -m "your message"
```
  