"""
Payment orchestration.

This is the most carefully-ordered file in the system.
Read the comments — every step is load-bearing.

The two-commit pattern:
  Commit 1: lock invoice, validate state, insert pending attempt → release lock
  [PSP call happens here, outside any transaction]
  Commit 2: update attempt status + invoice state

Why two commits:
  Holding a row lock across a 12s HTTP call to the PSP would block every
  other request on that invoice. With commit-1, the second concurrent /pay
  request can immediately acquire the lock, see the pending attempt, and
  decide independently.
"""
import hashlib
import json
import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import exceptions
from app.exceptions import (
    IdempotencyConflict,
    InvalidStateTransition,
    MissingIdempotencyKey,
    NotFoundError,
    PSPError,
)
from app.models.business import Business
from app.models.invoice import Invoice, InvoiceState
from app.models.payment import PaymentAttempt, PaymentStatus
from app.services import psp_client
from app.services.invoice_state import assert_transition_allowed


def _hash_request_body(card_token: str) -> str:
    """SHA-256 of the canonical request body for idempotency mismatch detection."""
    body = json.dumps({"card_token": card_token}, sort_keys=True)
    return hashlib.sha256(body.encode()).hexdigest()


def _build_cached_response(attempt: PaymentAttempt) -> dict:
    """Build the response dict from an existing attempt (idempotency cache hit)."""
    return {
        "attempt": attempt,
        "cached": True,
    }


async def process_payment(
    session: AsyncSession,
    business: Business,
    invoice_id: uuid.UUID,
    card_token: str,
    idempotency_key: str,
) -> tuple[PaymentAttempt, bool]:
    """
    Orchestrate a payment attempt.

    Returns: (attempt, is_cached)
    - is_cached=True means this is a replayed response, not a new charge.

    Raises:
    - NotFoundError: invoice not found or wrong tenant
    - InvalidStateTransition: invoice not in 'open' state
    - IdempotencyConflict: same key, different request body
    - MissingIdempotencyKey: header was absent (checked by router, but defensive)
    - PSPError: infrastructure failure (caller returns 202)
    """
    request_hash = _hash_request_body(card_token) 

    # ── T1: idempotency check + lock + insert pending ─────────────────────────

    # Step 1: check for existing attempt with this idempotency key
    existing = await session.execute(
        select(PaymentAttempt).where(
            PaymentAttempt.invoice_id == invoice_id,
            PaymentAttempt.idempotency_key == idempotency_key,
        )
    )
    existing_attempt = existing.scalar_one_or_none()

    if existing_attempt is not None:
        # Same key — verify the body matches
        if existing_attempt.request_hash != request_hash:
            raise IdempotencyConflict()
        # Cache hit — return the stored result without calling PSP again
        return existing_attempt, True

    # Step 2: acquire row-level lock on the invoice
    # FOR UPDATE blocks concurrent /pay calls until we commit.
    locked = await session.execute(
        select(Invoice).where(
            Invoice.id == invoice_id,
            Invoice.business_id == business.id,
        ).with_for_update()
    )
    invoice = locked.scalar_one_or_none()

    if invoice is None:
        raise NotFoundError("Invoice", invoice_id)

    # Step 3: validate the state machine allows payment
    # Raises InvalidStateTransition if state != 'open'
    assert_transition_allowed(invoice.state, InvoiceState.PAID)

    # Step 4: insert pending attempt
    attempt = PaymentAttempt(
        invoice_id=invoice_id,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        status=PaymentStatus.PENDING.value,
        amount_cents=invoice.total_cents,
        card_token=card_token,
    )
    session.add(attempt)

    try:
        await session.flush()
    except IntegrityError:
        # Race: another request with same idempotency key won the insert.
        # Roll back and let the caller retry — they'll hit the existing attempt.
        await session.rollback()
        raise IdempotencyConflict()

    # Step 5: COMMIT — releases the row lock.
    # The second concurrent request can now acquire the lock and see state.
    await session.commit()

    # ── PSP call — outside any transaction ───────────────────────────────────

    psp_result = None
    psp_error: PSPError | None = None

    try:
        psp_result = await psp_client.charge(
            card_token=card_token,
            amount_cents=attempt.amount_cents,
            idempotency_key=idempotency_key,
        )
    except PSPError as e:
        psp_error = e

    # ── T2: update attempt + invoice state ───────────────────────────────────

    # Re-fetch the attempt (session was committed, objects are expired)
    refreshed = await session.execute(
        select(PaymentAttempt).where(PaymentAttempt.id == attempt.id)
    )
    attempt = refreshed.scalar_one()

    if psp_error is not None:
        # Infrastructure failure (timeout or 5xx). Leave status='pending',
        # invoice stays 'open'. Return the attempt — pending is a normal
        # outcome, NOT an exception. Router maps it to HTTP 202.
        attempt.failure_code = (
            "psp_timeout" if "timeout" in type(psp_error).__name__.lower()
            else "psp_unavailable"
        )
        # No `raise` — fall through to the single commit at the end.

    elif psp_result.status == "succeeded":
        attempt.status = PaymentStatus.SUCCEEDED.value
        attempt.psp_ref = psp_result.psp_ref
        inv = await session.execute(
            select(Invoice).where(Invoice.id == invoice_id).with_for_update()
        )
        invoice = inv.scalar_one()
        assert_transition_allowed(invoice.state, InvoiceState.PAID)
        invoice.state = InvoiceState.PAID.value

    else:
        # Business failure (card_declined, insufficient_funds, etc).
        # Invoice stays 'open' — customer can retry with a different card.
        attempt.status = PaymentStatus.FAILED.value
        attempt.failure_code = psp_result.code

    await session.commit()
    await session.refresh(attempt)
    return attempt, False
