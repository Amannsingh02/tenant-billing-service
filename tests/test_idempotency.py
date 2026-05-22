"""
Idempotency tests.

1. Same key + same body → cached response (same attempt id)
2. Same key + different body → 409 Conflict
3. Missing Idempotency-Key header → 422
"""
import uuid

import httpx
import pytest

from tests.conftest import create_open_invoice


async def test_idempotency_same_key_same_body_returns_cached_result(auth_headers, customer_id):
    async with httpx.AsyncClient(base_url="http://localhost:8000", timeout=30.0) as client:
        invoice_id = await create_open_invoice(client, auth_headers, customer_id)
        idem_key = str(uuid.uuid4())
        headers = {**auth_headers, "Idempotency-Key": idem_key}

        # First call
        r1 = await client.post(
            f"/invoices/{invoice_id}/pay",
            headers=headers,
            json={"card_token": "tok_card_declined"},
        )
        assert r1.status_code == 402, f"Expected 402, got {r1.status_code}: {r1.text}"
        attempt_id_1 = r1.json()["id"]

        # Second call — same key, same body
        r2 = await client.post(
            f"/invoices/{invoice_id}/pay",
            headers=headers,
            json={"card_token": "tok_card_declined"},
        )
        assert r2.status_code == 402, f"Expected 402, got {r2.status_code}: {r2.text}"
        attempt_id_2 = r2.json()["id"]

        # Must return the SAME attempt
        assert attempt_id_1 == attempt_id_2, (
            f"Idempotency broken: got different attempt IDs "
            f"{attempt_id_1} vs {attempt_id_2}"
        )
        print(f"\nSame attempt ID returned: {attempt_id_1} ✓")


async def test_idempotency_key_reuse_with_different_body_returns_409(auth_headers, customer_id):
    async with httpx.AsyncClient(base_url="http://localhost:8000", timeout=30.0) as client:
        invoice_id = await create_open_invoice(client, auth_headers, customer_id)
        idem_key = str(uuid.uuid4())
        headers = {**auth_headers, "Idempotency-Key": idem_key}

        # First call with tok_card_declined
        r1 = await client.post(
            f"/invoices/{invoice_id}/pay",
            headers=headers,
            json={"card_token": "tok_card_declined"},
        )
        assert r1.status_code == 402

        # Second call with DIFFERENT card token but SAME idempotency key
        r2 = await client.post(
            f"/invoices/{invoice_id}/pay",
            headers=headers,
            json={"card_token": "tok_success"},
        )
        assert r2.status_code == 409, (
            f"Expected 409 Conflict, got {r2.status_code}: {r2.text}"
        )
        assert "different" in r2.json()["detail"].lower() or \
               "reused" in r2.json()["detail"].lower(), \
            f"Expected conflict message, got: {r2.json()['detail']}"
        print(f"\n409 returned correctly: {r2.json()['detail']} ✓")


async def test_idempotency_key_required(auth_headers, customer_id):
    async with httpx.AsyncClient(base_url="http://localhost:8000", timeout=30.0) as client:
        invoice_id = await create_open_invoice(client, auth_headers, customer_id)

        # No Idempotency-Key header
        r = await client.post(
            f"/invoices/{invoice_id}/pay",
            headers=auth_headers,
            json={"card_token": "tok_success"},
        )
        assert r.status_code == 422, (
            f"Expected 422, got {r.status_code}: {r.text}"
        )
        print(f"\n422 returned correctly for missing key ✓")