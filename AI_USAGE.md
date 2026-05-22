## Tools Used

**Claude (Anthropic)** — primary tool. Used for architecture conversations, design tradeoff discussions, and generating scaffolding for boilerplate-heavy files. Every generated file was read and in several cases corrected before committing.

**ChatGPT (OpenAI)** — occasional targeted lookups. Used to understand `SELECT FOR UPDATE SKIP LOCKED` behavior and cross-check the HMAC-SHA256 signing approach against what Stripe actually ships. Did not generate code from ChatGPT.

No Cursor, Copilot, or inline editor AI was used.

---

## Three Decisions I Made Independently

### Decision 1 — Removed `business_id` from API responses

The AI included `business_id` in `CustomerRead` and `InvoiceRead` by default. I removed it. The client authenticates with an API key already scoped to their business — they know their own tenant. Returning `business_id` is noise and a minor information leak. The response shape should reflect what the client needs, not what is convenient to serialize from the ORM object.

### Decision 2 — Kept tests against real Postgres, not SQLite

The AI suggested SQLite in-memory for speed. I kept tests pointed at `localhost:8000` against the live Docker stack. The concurrency test exists specifically to verify `SELECT FOR UPDATE` serializes concurrent writes. SQLite does not implement row-level locking the same way — a passing concurrency test on SQLite would be meaningless. The timeout test also needs a real PSP mock over the network. False confidence on exactly the tests that matter most is worse than no tests.

### Decision 3 — Caught the missing Alembic volume mount

When migration files were generating inside the container but not appearing locally, I diagnosed that `alembic/` was not bind-mounted in `docker-compose.yml`. The AI had set up `./app:/app/app` but never mounted `./alembic:/app/alembic`. Every migration was being generated inside a throwaway container and lost on restart. I identified the gap, added the mount, and re-ran the generation.

---

## One Thing the AI Got Wrong

### The concurrent payment bug in `payment_service.py`

The AI claimed `SELECT FOR UPDATE` combined with validating `state == 'open'` before inserting the pending attempt was sufficient to prevent double charges. When I ran the concurrency test — 5 simultaneous `/pay` requests — all 5 returned `200 OK`. Five succeeded attempts, one invoice.

The problem: state validation happened in transaction 1 before the commit. The second concurrent request could read `state=open` before the first request's changes were visible. The state re-check after the PSP call was missing — if the first request transitioned the invoice to `paid` in its second commit, the second request's post-PSP update would blindly write `paid` again without re-validating.

The fix: add `assert_transition_allowed` inside the second transaction, after re-fetching the invoice with `FOR UPDATE`. The lesson: the two-commit pattern breaks the assumption that "I validated state at the start so it is still valid now." Every state write needs its own validation at write time, not just read time.

---

## Summary

AI was useful for generating boilerplate and explaining internals. It was not reliable for getting subtle correctness right on the first attempt around transaction boundaries and concurrent state mutation. Those required reading the code carefully, running the tests, and understanding why gaps existed rather than just applying patches.