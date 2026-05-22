"""
Payment orchestration.

The most carefully-ordered file in the system. Read the comments — every
step is load-bearing.

The two-commit pattern:
 Commit 1: lock invoice, validate state, insert pending attempt → release lock.
 [PSP call happens OUTSIDE any transaction]
 Commit 2: update attempt status + invoice state (+ enqueue webhook).

Why two commits:
 Holding a row lock across a 12s HTTP call to the PSP would block every
 other request on that invoice for the duration. With commit-1, the
 second concurrent /pay request can immediately acquire the lock, see
 the (now durable) pending attempt, and decide independently.

PSP failure handling:
 PSP timeouts and 5xx errors leave the attempt in 'pending' state and the
 invoice in 'open' state. The router maps a pending attempt to HTTP 202
 Accepted — "we received your request but the outcome is unknown."

 Pending is a NORMAL outcome, never raised as an exception out of the
 service. We never charge twice and never corrupt invoice state on failure.
"""

import hashlib
import json
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import (
    IdempotencyConflict,
    InvalidStateTransition,
    NotFoundError,
)

from app.models.business import Business
from app.models.invoice import Invoice, InvoiceState
from app.models.payment import PaymentAttempt, PaymentStatus

from app.services import psp_client
from app.services.invoice_state import assert_transition_allowed
from app.services.psp_client import PSPError


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------

def _hash_request_body(card_token: str) -> str:
    """
    SHA-256 of the canonical request body for idempotency mismatch detection.

    Only card_token contributes — amount comes from the invoice and the
    idempotency_key is the lookup key, not part of the hash.
    """

    body = json.dumps({"card_token": card_token}, sort_keys=True)
    return hashlib.sha256(body.encode()).hexdigest()


def _failure_code_from_psp_error(e: PSPError) -> str:
    """Map a typed PSP exception to a stable failure_code string."""

    name = type(e).__name__.lower()

    if "timeout" in name:
        return "psp_timeout"

    return "psp_unavailable"


# ----------------------------------------------------------------------------
# Main orchestration
# ----------------------------------------------------------------------------

async def process_payment(
    session: AsyncSession,
    business: Business,
    invoice_id: uuid.UUID,
    card_token: str,
    idempotency_key: str,
) -> tuple[PaymentAttempt, bool]:
    """
    Orchestrate a payment attempt end-to-end.

    Returns:
        (attempt, is_cached)

        - attempt : PaymentAttempt with terminal-for-this-call status
          ('succeeded' | 'failed' | 'pending')

        - is_cached : True if this is a replayed response from a prior call
          with the same idempotency_key (no new PSP call).

    Raises:
        NotFoundError — invoice doesn't exist for this business.
        InvalidStateTransition — invoice is not in 'open' state.
        IdempotencyConflict — same key used with a different request body.
    """

    request_hash = _hash_request_body(card_token)

    # ── STEP 1: idempotency cache check ──────────────────────────────────
    # Look up by (invoice_id, idempotency_key) before doing anything.
    # If found, return cached result without touching the PSP.

    existing = await session.execute(
        select(PaymentAttempt).where(
            PaymentAttempt.invoice_id == invoice_id,
            PaymentAttempt.idempotency_key == idempotency_key,
        )
    )

    existing_attempt = existing.scalar_one_or_none()

    if existing_attempt is not None:
        if existing_attempt.request_hash != request_hash:
            # Same key, different body — client bug. Refuse rather than
            # silently serving a result for a different request.
            raise IdempotencyConflict()

        return existing_attempt, True

    # ── STEP 2: lock invoice, validate state, insert pending attempt ─────

    # SELECT FOR UPDATE serializes concurrent /pay requests on the same
    # invoice. First wins the lock, validates state, inserts pending,
    # commits, releases the lock. Second blocks, then acquires the lock,
    # sees the now-pending or now-paid state, decides independently.

    locked = await session.execute(
        select(Invoice).where(
            Invoice.id == invoice_id,
            Invoice.business_id == business.id,
        ).with_for_update()
    )

    invoice = locked.scalar_one_or_none()

    if invoice is None:
        raise NotFoundError(f"Invoice {invoice_id} not found")

    # State-machine check: only 'open' invoices can be paid.
    # Raises InvalidStateTransition otherwise.

    assert_transition_allowed(invoice.state, InvoiceState.PAID)

    # Insert the pending attempt. The UNIQUE(invoice_id, idempotency_key)
    # constraint is the DB-level safety net if our app-level check missed
    # a race.

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

    except IntegrityError as e:
        await session.rollback()

        # Two unique constraints could fire here:
        #
        # 1. (invoice_id, idempotency_key)
        #    → same key reused concurrently
        #
        # 2. partial unique index:
        #    uq_payment_attempts_one_pending_per_invoice
        #    → another payment is already in-flight

        error_msg = str(e.orig) if e.orig else str(e)

        if "one_pending_per_invoice" in error_msg:
            raise InvalidStateTransition(
                "open",
                "paid",
                reason="another payment attempt is already in progress for this invoice",
            )

        # Otherwise it's the idempotency-key race.
        # Re-fetch the winning row and return cached response.

        existing = await session.execute(
            select(PaymentAttempt).where(
                PaymentAttempt.invoice_id == invoice_id,
                PaymentAttempt.idempotency_key == idempotency_key,
            )
        )

        winner = existing.scalar_one_or_none()

        if winner is not None and winner.request_hash == request_hash:
            return winner, True

        raise IdempotencyConflict()

    # CRITICAL COMMIT: makes the pending attempt durable, releases the
    # row lock on the invoice. We will NOT hold a DB connection lock
    # across the PSP call.

    await session.commit()

    # ── STEP 3: call the PSP (no lock held) ──────────────────────────────

    psp_result: psp_client.PSPChargeResult | None = None
    psp_error: PSPError | None = None

    try:
        psp_result = await psp_client.charge(
            card_token=card_token,
            amount_cents=attempt.amount_cents,
            idempotency_key=idempotency_key,
        )

    except PSPError as e:
        psp_error = e

    # ── STEP 4: resolve outcome and persist ──────────────────────────────

    # Re-fetch the attempt freshly in case anything is stale after commit-1.

    refreshed = await session.execute(
        select(PaymentAttempt).where(PaymentAttempt.id == attempt.id)
    )

    attempt = refreshed.scalar_one()

    # Inline import to avoid potential circular dependency at module load.
    from app.services import webhook_service

    if psp_error is not None:
        # Infrastructure failure (timeout or 5xx). Leave status='pending',
        # invoice stays 'open'. Return — pending is a NORMAL outcome, NOT
        # an exception. Router maps it to HTTP 202.
        # No webhook event for pending — only fire on known outcomes.

        attempt.failure_code = _failure_code_from_psp_error(psp_error)

    elif psp_result.status == "succeeded":
        attempt.status = PaymentStatus.SUCCEEDED.value
        attempt.psp_ref = psp_result.psp_ref

        # Re-lock the invoice for the state transition. Defense in depth
        # against an unlikely concurrent void between commit-1 and now.

        inv = await session.execute(
            select(Invoice).where(Invoice.id == invoice_id).with_for_update()
        )

        invoice = inv.scalar_one()

        assert_transition_allowed(invoice.state, InvoiceState.PAID)

        invoice.state = InvoiceState.PAID.value

        await webhook_service.enqueue_event(
            db=session,
            business_id=business.id,
            event_type="invoice.paid",
            payload={
                "invoice_id": str(invoice_id),
                "amount_cents": attempt.amount_cents,
                "psp_ref": psp_result.psp_ref,
            },
        )

    else:
        # Business failure (card_declined, insufficient_funds, etc).
        # Invoice stays 'open' — customer can retry with a different card.

        attempt.status = PaymentStatus.FAILED.value
        attempt.failure_code = psp_result.code

        await webhook_service.enqueue_event(
            db=session,
            business_id=business.id,
            event_type="invoice.payment_failed",
            payload={
                "invoice_id": str(invoice_id),
                "failure_code": psp_result.code,
                "amount_cents": attempt.amount_cents,
            },
        )

    # Single commit at the end. Single refresh so Pydantic serialization
    # in the router has all attributes loaded (otherwise greenlet error).

    await session.commit()
    await session.refresh(attempt)

    return attempt, False