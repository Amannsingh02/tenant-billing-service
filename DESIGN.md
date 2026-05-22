# Design Document — Tenant Billing Service

The primary deliverable. Covers data model, state machine, payment correctness, webhook design, API key model, what I cut, and the production-readiness gap.

> Conventions: money is integer cents in `BIGINT` (no floats anywhere). UUIDs are server-generated via `pgcrypto`. Timestamps are `TIMESTAMP WITH TIME ZONE`. Tenant-scoped queries always filter by `business_id`. State columns are `VARCHAR` + `CHECK` constraint.

---

## 1. Data Model

8 tables. Full schema in `alembic/versions/`; the interesting columns and indexes:

```
businesses (id PK, name, timestamps)

api_keys
  business_id FK CASCADE        [idx]
  key_hash    char(64) UNIQUE   ← SHA-256 hex, used for auth lookup
  key_prefix  varchar(20)       ← "sk_live_abc1", safe to log
  is_active, revoked_at, created_at

customers
  business_id FK CASCADE        [idx]
  UNIQUE (business_id, email)   ← email unique PER tenant, not globally

invoices
  business_id FK CASCADE
  customer_id FK RESTRICT       [idx]
  state       varchar(20)  CHECK IN (draft|open|paid|void|uncollectible)
  total_cents bigint  CHECK >= 0   ← server-computed; client never supplies
  INDEX (business_id, state)    ← list-by-state hot path
  INDEX (business_id, created_at)

invoice_line_items
  invoice_id FK CASCADE         [idx]
  quantity int CHECK > 0
  unit_amount_cents bigint CHECK >= 0
  amount_cents bigint CHECK = quantity * unit_amount_cents

payment_attempts
  invoice_id FK RESTRICT        ← audit trail; never deletable
  idempotency_key varchar(255)
  request_hash    char(64)      ← SHA-256(canonical body) — mismatch detection
  status, psp_ref, failure_code, amount_cents, card_token
  UNIQUE (invoice_id, idempotency_key)
  PARTIAL UNIQUE (invoice_id) WHERE status='pending'   ← see §3a

webhook_endpoints (business_id FK, url, signing_secret, is_active)

webhook_deliveries (the outbox)
  webhook_endpoint_id FK CASCADE
  event_type, payload (jsonb)
  status varchar(20) CHECK IN (pending|delivered|exhausted)
  attempts, next_attempt_at, last_error
  INDEX (status, next_attempt_at)   ← worker's hot polling query
```

**Key decisions:**

- **`VARCHAR + CHECK` over Postgres `ENUM`.** `ALTER TYPE` couples schema to deploys and rolling back is painful; CHECK gives the same enforcement at lower evolution cost.
- **`BIGINT` cents.** Python ints are arbitrary precision; the column type makes float values impossible.
- **`business_id` denormalized on `invoices` and `payment_attempts`.** Eliminates join cost on tenant-scoped queries.
- **No ORM relationships unless needed; explicit `selectinload` where required.** Async lazy-loading is a footgun. `Invoice.line_items` uses `lazy="raise"` to fail loud on accidental implicit access.

**At 100x scale:** partition `invoices` and `payment_attempts` by `business_id` (or by month); switch to keyset pagination from offset (`OFFSET` is O(n) at depth); move the outbox to a real queue and archive delivered rows. Today's indexes already match these partition keys.

---

## 2. Invoice State Machine

```
       ┌────────┐
       │ DRAFT  │
       └───┬────┘
           │ finalize
           ▼
       ┌────────┐
       │  OPEN  │◄─── pay returns 'failed' (record-only,
       └───┬────┘     state DOES NOT change)
  pay  ┌───┼───────┐
  ┌────┘   │ void  └────┐ mark uncollectible
  ▼        ▼            ▼
┌──────┐ ┌──────┐ ┌─────────────┐
│ PAID │ │ VOID │ │UNCOLLECTIBLE│
└──────┘ └──────┘ └─────────────┘
TERMINAL  TERMINAL  TERMINAL
```

| From → To              | Trigger                                  | Reversible |
| ---------------------- | ---------------------------------------- | ---------- |
| draft → open           | `POST /invoices/{id}/finalize`           | No         |
| open → paid            | `POST /pay` returns `succeeded`          | No         |
| open → void            | `POST /invoices/{id}/void`               | No         |
| open → uncollectible   | `POST /invoices/{id}/uncollectible`      | No         |
| open → open (no change)| `POST /pay` returns `failed` or PSP error | —         |

Terminal states have no outgoing transitions. The state machine is a pure-function module (`app/services/invoice_state.py`); `assert_transition_allowed(current, target)` raises `InvalidStateTransition` → **HTTP 422**. The DB encodes the value set as a `CHECK` constraint — defense in depth.

---

## 3. Payment Correctness & Failure Modes

### (a) Two clients call `POST /pay` simultaneously

**Outcome:** Exactly one succeeds (200). Others rejected with 422. Invoice ends `paid`. No double charge. Verified by `tests/test_concurrency.py` firing 5 concurrent requests.

**Mechanism — two-layer defense:**

1. **`SELECT ... FOR UPDATE`** on the invoice row at the start. Serializes lock acquisition.
2. **`PARTIAL UNIQUE INDEX (invoice_id) WHERE status='pending'`** on `payment_attempts`. At-most-one in-flight per invoice at the storage layer.

**Why both?** `SELECT FOR UPDATE` serializes lock acquisition, but invoice state doesn't change until *after* the PSP call. Without the partial index, requests 2..N could acquire the lock, see `state=open` (still), and proceed. The partial unique index closes this gap: first INSERT wins, the rest fail at the constraint, service translates to 422. The test suite revealed this — the lock-only design didn't actually prevent concurrent acceptance.

### (b) PSP times out (`tok_timeout` sleeps 30s)

**Outcome:** Endpoint returns within ~12s (`PSP_TIMEOUT_SECONDS`) with **HTTP 202 Accepted**. The `payment_attempt` stays `status=pending, failure_code=psp_timeout`. Invoice stays `state=open`. Caller can poll or (in production) wait for a webhook.

**Mechanism:** `httpx.AsyncClient(timeout=12.0)` bounds the wait. `TimeoutException` is caught and translated to a typed `PSPTimeout`. `payment_service` treats this as a **normal outcome** (not an exception out of the service): set `failure_code`, fall through to the same commit + return path as success/failure. Router maps `pending` → 202. Treating pending as normal is deliberate — earlier drafts raised out of the service, which leaked stack traces and produced 500s. The system genuinely doesn't know whether the PSP charged.

### (c) PSP succeeds but our service crashes before persisting

On retry with the same idempotency key, the cache check finds the existing pending attempt and returns it unchanged. We do **not** call the PSP again — that would risk a double charge.

**Known gap:** the pending attempt sits in the DB; a human or reconciliation job has to resolve it against the PSP's transaction log. **Production fix:** (1) pass our idempotency key to the PSP (Stripe accepts this) — guarantees a single charge per key; (2) a nightly job that resolves pending attempts older than N minutes against the PSP's status endpoint. Our mock PSP is deterministic per `card_token`, which papers over this end-to-end — design gap is real and documented.

### (d) Idempotency key reused with a different request body

**Outcome:** HTTP 409 Conflict. We store `request_hash = SHA-256(canonical body)` on the attempt. On retry: match → return cached attempt; mismatch → 409. Silently serving the cached result for a different request would be a correctness bug.

### (e) Invoice in `paid` state receives another `POST /pay`

**Outcome:** HTTP 422 — state machine refuses before any PSP call. Same for `void` and `uncollectible`.

### Why this concurrency mechanism over alternatives

| Alternative | Why not |
| --- | --- |
| Advisory locks | App-only; bypassed by ad-hoc queries, migrations, ops |
| Optimistic concurrency | Two writers both see `open`; needs version column to be correct |
| `SERIALIZABLE` isolation | Heavy; arbitrary 40001 retries; large blast radius |
| Status-conditional update only | Same race as optimistic + harder to make idempotent |

Row-level `FOR UPDATE` + partial unique index is the precise tool: app-level serialization with a DB-level safety net.

---

## 4. Webhook Design

### Signing

`HMAC-SHA256(secret, f"{timestamp}.{body}")`. Headers on every delivery:

```
X-Webhook-Timestamp: 1779415532
X-Webhook-Signature: t=1779415532,v1=a5bf58d8...c411
```

**Timestamp included for replay protection.** Without it, a captured signature is valid forever. Recommended receiver behavior: reject signatures older than 5 minutes. Comparison uses `hmac.compare_digest` (constant-time).

### Retry policy

```
attempt 1: immediate
attempt 2: +1s
attempt 3: +5s
attempt 4: +25s
attempt 5: +125s
  → give up (status='exhausted')
```

Max 5 attempts, ~155s total budget. Failures that trigger retry: any non-2xx, connection error, timeout. Stripe is more aggressive (~3 days); 155s is appropriate for take-home scale — long enough to absorb transient blips, short enough to surface incidents in alerting windows.

**Reconciliation for missed/exhausted events:** the underlying resource state is the source of truth — a business that missed `invoice.paid` can `GET /invoices/{id}` and see `state=paid`. A future `GET /webhook_deliveries?status=exhausted` would let them replay.

### Why delivery is decoupled from the API path

The `POST /pay` handler writes the `webhook_deliveries` row **in the same transaction** as the state change. It does **not** make the outgoing HTTP call. A separate worker (in-process asyncio loop) polls `WHERE status='pending' AND next_attempt_at <= NOW()` every 2s, claims batches with `FOR UPDATE SKIP LOCKED` (multi-worker safe), signs, and POSTs.

Reasons: (1) `/pay` latency would otherwise be PSP + slowest-receiver; (2) receiver downtime can't fail our payment; (3) outbox in the same transaction means we cannot have "state changed but no event" or vice versa; (4) synchronous delivery has no place to retry — the request is gone after response.

This is the **outbox pattern**. The worker runs in the same process via FastAPI `lifespan` — durable but doesn't scale horizontally. Production would extract it; Redis stays in compose as a forward-compatibility placeholder.

---

## 5. API Key Model

`secrets.token_urlsafe(24)` (≈192 bits) prefixed with `sk_live_` (Stripe-style; helps GitHub secret scanning). Only the SHA-256 hex digest is stored; the plaintext is shown once at creation, never logged. The first 12 chars (e.g. `sk_live_a3X9`) are stored as `key_prefix` for safe display in support / audit. Transmission: `Authorization: Bearer <key>`; TLS termination is upstream.

**Why SHA-256, not bcrypt:** API keys are already maximum-entropy random secrets. Slow hashing buys no security; it adds latency to every authenticated request. Bcrypt/argon2 are for low-entropy human inputs where slowness is the point. Stripe, GitHub, AWS all use unsalted fast hashes for API keys. Comparison uses `hmac.compare_digest` — constant-time by reflex.

**Rotation/revocation:** rotate = new key (plaintext shown once) + deactivate old. Revoke = `is_active=false, revoked_at=now()`. Auth rejects revoked keys with the same generic 401 as any other failure — no information leakage.

**Blast radius if leaked:** full read/write on the leaking business's data — nothing else. Every query scopes by `business.id`; cross-tenant lookups return 404 (never 403), so a leaked key can't even confirm the existence of other tenants.

---

## 6. What I Cut and Why

1. **Refunds and partial payments.** Would need a `refunds` table, new state values, dedicated webhook events, partial-amount tracking. Stubbing cheaply would create a worse data model than no model.
2. **Sign-up flow.** Bootstrapping the first business + API key is a separate auth concern (email verification, password/OAuth, audit log). Token API auth is the right scope per spec; bootstrap documented in README via psql.
3. **Cursor pagination.** Offset is simpler and fine at this scale. At 100k+ invoices per business, switch to keyset using `(created_at, id)`.
4. **Webhook worker as separate process.** Today it's in-process via `lifespan` — durable (outbox in Postgres) but doesn't scale horizontally. Production: Celery or standalone Python service.
5. **Test coverage beyond required three.** Per spec — coverage doesn't score. Three tests (concurrency, idempotency, PSP failure) exercise the hardest parts.

---

## 7. Production Readiness Gap

Top three missing if shipped tomorrow:

**1. Observability.** `structlog` is installed but not wired in. Need structured JSON logs with `request_id` + `business_id` via context vars, OpenTelemetry traces (especially across the FastAPI → PSP boundary), and metrics (payment success rate, p99 latency, webhook delivery success, outbox lag). Without these, "is the PSP slow today?" is unanswerable.

**2. Rate limiting per API key.** Today a noisy or compromised key can pin the connection pool. Production: leaky-bucket per-key limits backed by Redis, with `429` + `Retry-After` headers.

**3. Secret encryption at rest.** `signing_secret` and `card_token` are plaintext. Production: column-level encryption (pgcrypto or app-side AES-GCM with KMS) plus a key rotation story.

**Honorable mentions:** audit log table for state transitions and key events; automatic dunning / retry workflow on payment failure; a consistent `{error: {code, message, request_id}}` error envelope.

---

## Closing

Three decisions I'd defend in an interview:

1. **Two-layer concurrency control** — `SELECT FOR UPDATE` for app-level serialization, partial unique index for DB-level enforcement. Either alone is insufficient.
2. **Outbox pattern for webhooks** — same-transaction enqueue, decoupled delivery. Durable, atomic, retryable.
3. **Pending is a normal outcome** — PSP timeout and network errors return `pending` + HTTP 202. The system tolerates not knowing.

Next, in priority order: PSP-side idempotency + reconciliation job (closes failure-mode c), structured logging end-to-end, per-key rate limiting.
