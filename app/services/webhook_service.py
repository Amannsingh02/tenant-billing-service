"""
Webhook service.

Two responsibilities:
1. Endpoint CRUD (register, list, deactivate)
2. enqueue_event — called by other services to write outbox rows
   in the SAME transaction as the domain event.

enqueue_event fans out to all active endpoints for the business.
One webhook_deliveries row per (event, endpoint) pair.
"""
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.business import Business
from app.models.webhook import WebhookDelivery, WebhookDeliveryStatus, WebhookEndpoint


def _generate_signing_secret() -> str:
    """32 bytes of URL-safe random = ~192 bits entropy."""
    return secrets.token_urlsafe(32)


async def create_endpoint(
    session: AsyncSession,
    business: Business,
    url: str,
) -> tuple[WebhookEndpoint, str]:
    """
    Register a new webhook endpoint.
    Returns (endpoint, plaintext_secret).
    The plaintext secret is shown to the user ONCE and never stored.
    We store it directly here (in a real system you'd encrypt at rest).
    """
    signing_secret = _generate_signing_secret()
    endpoint = WebhookEndpoint(
        business_id=business.id,
        url=url,
        signing_secret=signing_secret,
        is_active=True,
    )
    session.add(endpoint)
    await session.flush()
    return endpoint, signing_secret


async def list_endpoints(
    session: AsyncSession,
    business: Business,
) -> list[WebhookEndpoint]:
    result = await session.execute(
        select(WebhookEndpoint).where(
            WebhookEndpoint.business_id == business.id,
            WebhookEndpoint.is_active.is_(True),
        )
    )
    return list(result.scalars().all())


async def deactivate_endpoint(
    session: AsyncSession,
    business: Business,
    endpoint_id: uuid.UUID,
) -> WebhookEndpoint | None:
    result = await session.execute(
        select(WebhookEndpoint).where(
            WebhookEndpoint.id == endpoint_id,
            WebhookEndpoint.business_id == business.id,
        )
    )
    endpoint = result.scalar_one_or_none()
    if endpoint is None:
        return None
    endpoint.is_active = False
    await session.flush()
    return endpoint


async def enqueue_event(
    db: AsyncSession,
    business_id: uuid.UUID,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    """
    Write outbox rows for a domain event — one per active endpoint.

    MUST be called inside an open transaction (before commit).
    The outbox rows are written atomically with the domain change.
    If the caller rolls back, these rows roll back too.
    """
    result = await db.execute(
        select(WebhookEndpoint).where(
            WebhookEndpoint.business_id == business_id,
            WebhookEndpoint.is_active.is_(True),
        )
    )
    endpoints = list(result.scalars().all())

    if not endpoints:
        return

    now = datetime.now(timezone.utc)
    for endpoint in endpoints:
        delivery = WebhookDelivery(
            webhook_endpoint_id=endpoint.id,
            event_type=event_type,
            payload=payload,
            status=WebhookDeliveryStatus.PENDING.value,
            attempts=0,
            next_attempt_at=now,
        )
        db.add(delivery)