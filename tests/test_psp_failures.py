"""
PSP failure tests.

1. tok_network_error → 202, invoice stays open, attempt stays pending
2. tok_timeout → returns within ~12s (NOT 30s), invoice stays open
"""
import time
import uuid

import httpx
import pytest

from tests.conftest import create_open_invoice


async def test_psp_network_error_leaves_invoice_open(auth_headers, customer_id):
    async with httpx.AsyncClient(base_url="http://localhost:8000", timeout=30.0) as client:
        invoice_id = await create_open_invoice(client, auth_headers, customer_id)

        r = await client.post(
            f"/invoices/{invoice_id}/pay",
            headers={**auth_headers, "Idempotency-Key": str(uuid.uuid4())},
            json={"card_token": "tok_network_error"},
        )

        # Must not hang or 500 — returns 202 (pending/unavailable)
        assert r.status_code == 202, (
            f"Expected 202 for network error, got {r.status_code}: {r.text}"
        )

        # Invoice must still be open — not corrupted
        inv = await client.get(f"/invoices/{invoice_id}", headers=auth_headers)
        assert inv.json()["state"] == "open", (
            f"Invoice state should still be 'open', got: {inv.json()['state']}"
        )
        print(f"\nNetwork error handled correctly, invoice state: {inv.json()['state']} ✓")


async def test_psp_timeout_returns_within_our_timeout_not_psps(auth_headers, customer_id):
    """
    tok_timeout makes the PSP sleep 30s.
    Our httpx timeout is 12s.
    This test must complete in ~12s, NOT 30s.
    """
    async with httpx.AsyncClient(base_url="http://localhost:8000", timeout=30.0) as client:
        invoice_id = await create_open_invoice(client, auth_headers, customer_id)

        start = time.time()
        r = await client.post(
            f"/invoices/{invoice_id}/pay",
            headers={**auth_headers, "Idempotency-Key": str(uuid.uuid4())},
            json={"card_token": "tok_timeout"},
        )
        elapsed = time.time() - start
        print(f"\nTimeout test elapsed: {elapsed:.1f}s")

        # Must return within our timeout window (12s) + some slack
        assert elapsed < 20, (
            f"Request took {elapsed:.1f}s — endpoint is hanging on PSP timeout. "
            f"Expected ~12s."
        )

        # Must return 202 — we accepted but don't know outcome
        assert r.status_code == 202, (
            f"Expected 202 for timeout, got {r.status_code}: {r.text}"
        )

        # Invoice must still be open
        inv = await client.get(f"/invoices/{invoice_id}", headers=auth_headers)
        assert inv.json()["state"] == "open", (
            f"Invoice state should still be 'open', got: {inv.json()['state']}"
        )
        print(f"Invoice state: {inv.json()['state']} ✓")
        print(f"Completed in {elapsed:.1f}s (PSP sleeps 30s) ✓")