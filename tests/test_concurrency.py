"""
Concurrency test — N simultaneous /pay requests on the same invoice.
Exactly one must succeed. The rest must be rejected.
No double charges.

Mechanism under test: SELECT FOR UPDATE row-level lock.
"""
import asyncio
import uuid

import httpx
import pytest

from tests.conftest import create_open_invoice

N_CONCURRENT = 5


async def pay(client: httpx.AsyncClient, headers: dict, invoice_id: str) -> httpx.Response:
    return await client.post(
        f"/invoices/{invoice_id}/pay",
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
        json={"card_token": "tok_success"},
    )


async def test_concurrent_payments_at_most_one_succeeds(auth_headers, customer_id):
    async with httpx.AsyncClient(base_url="http://localhost:8000", timeout=30.0) as client:
        invoice_id = await create_open_invoice(client, auth_headers, customer_id)
        print(f"\nInvoice: {invoice_id}")

        # Fire N concurrent requests simultaneously
        tasks = [pay(client, auth_headers, invoice_id) for _ in range(N_CONCURRENT)]
        responses = await asyncio.gather(*tasks)

        status_codes = [r.status_code for r in responses]
        print(f"Status codes: {status_codes}")

        successes = [r for r in responses if r.status_code == 200]
        failures = [r for r in responses if r.status_code in (402, 422)]

        print(f"Successes: {len(successes)}, Failures: {len(failures)}")

        # Exactly one must succeed
        assert len(successes) == 1, (
            f"Expected exactly 1 success, got {len(successes)}. "
            f"Status codes: {status_codes}"
        )

        # All others must be rejected (not 500s)
        assert len(failures) == N_CONCURRENT - 1, (
            f"Expected {N_CONCURRENT - 1} rejections, got {len(failures)}. "
            f"Status codes: {status_codes}"
        )

        # Invoice must be paid
        inv_resp = await client.get(f"/invoices/{invoice_id}", headers=auth_headers)
        assert inv_resp.json()["state"] == "paid", (
            f"Invoice state should be 'paid', got: {inv_resp.json()['state']}"
        )

        print(f"Invoice state: {inv_resp.json()['state']} ✓")