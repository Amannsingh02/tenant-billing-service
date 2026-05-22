"""
Test fixtures. Tests run against the live stack (localhost:8000).
Requires: docker compose up -d before running pytest.
"""
import asyncio
import uuid

import httpx
import pytest

BASE_URL = "http://localhost:8000"

# These are seeded once and reused across all tests in the session.
# Set these to your existing business key + customer ID,
# or let the fixtures create fresh ones via the DB.
from dotenv import load_dotenv
import os

load_dotenv()

EXISTING_KEY = os.getenv("EXISTING_KEY")
EXISTING_CUSTOMER_ID = os.getenv("EXISTING_CUSTOMER_ID")


@pytest.fixture(scope="session")
def api_key() -> str:
    return EXISTING_KEY


@pytest.fixture(scope="session")
def customer_id() -> str:
    return EXISTING_CUSTOMER_ID


@pytest.fixture(scope="session")
def auth_headers(api_key) -> dict:
    return {"Authorization": f"Bearer {api_key}"}


@pytest.fixture
async def client():
    """Fresh async HTTP client per test."""
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30.0) as c:
        yield c


async def create_open_invoice(client: httpx.AsyncClient, headers: dict, customer_id: str) -> str:
    """Helper: create and finalize an invoice, return its id."""
    resp = await client.post(
        "/invoices",
        headers=headers,
        json={
            "customer_id": customer_id,
            "line_items": [
                {"description": "Test item", "quantity": 1, "unit_amount_cents": 5000}
            ],
        },
    )
    assert resp.status_code == 201, f"Invoice create failed: {resp.text}"
    invoice_id = resp.json()["id"]

    resp = await client.post(f"/invoices/{invoice_id}/finalize", headers=headers)
    assert resp.status_code == 200, f"Finalize failed: {resp.text}"

    return invoice_id