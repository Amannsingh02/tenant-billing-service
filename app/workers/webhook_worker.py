"""
Async webhook delivery worker.

Runs as a background task in the same process as the API.
Polls webhook_deliveries WHERE status='pending' AND next_attempt_at <= NOW()
using SELECT FOR UPDATE SKIP LOCKED for concurrency safety.

Retry policy:
  Max attempts: 5
  Backoff: exponential — 1s, 5s, 25s, 125s, 625s
  After exhaustion: status='exhausted', stops retrying

In production: run as dedicated worker processes with Celery.
"""
import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select

from app.database import async_session_factory
from app.models.webhook import WebhookDelivery, WebhookDeliveryStatus, WebhookEndpoint
from app.utils.signing import sign_payload

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
BACKOFF_SECONDS = [1, 5, 25, 125, 625]
POLL_INTERVAL = 2  # seconds between polls
DELIVERY_TIMEOUT = 10  # seconds per HTTP request


class WebhookWorker:
    def __init__(self):
        self._task: asyncio.Task | None = None
        self._running = False

    def start(self):
        self._running = True
        self._task = asyncio.create_task(self._run())
        logger.info("Webhook worker started")

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Webhook worker stopped")

    async def _run(self):
        while self._running:
            try:
                await self._poll()
            except Exception as e:
                logger.error(f"Webhook worker poll error: {e}")
            await asyncio.sleep(POLL_INTERVAL)

    async def _poll(self):
        async with async_session_factory() as session:
            async with session.begin():
                # SELECT FOR UPDATE SKIP LOCKED — safe for multiple workers
                result = await session.execute(
                    select(WebhookDelivery)
                    .where(
                        WebhookDelivery.status == WebhookDeliveryStatus.PENDING.value,
                        WebhookDelivery.next_attempt_at <= datetime.now(timezone.utc),
                    )
                    .order_by(WebhookDelivery.next_attempt_at)
                    .limit(10)
                    .with_for_update(skip_locked=True)
                )
                deliveries = list(result.scalars().all())

                for delivery in deliveries:
                    await self._deliver(session, delivery)

    async def _deliver(self, session, delivery: WebhookDelivery):
        # Load the endpoint to get URL + signing secret
        result = await session.execute(
            select(WebhookEndpoint).where(
                WebhookEndpoint.id == delivery.webhook_endpoint_id
            )
        )
        endpoint = result.scalar_one_or_none()
        if endpoint is None:
            delivery.status = WebhookDeliveryStatus.EXHAUSTED.value
            delivery.last_error = "endpoint not found"
            return

        body = json.dumps(delivery.payload, separators=(",", ":"))
        signature_header, timestamp = sign_payload(endpoint.signing_secret, body)

        headers = {
            "Content-Type": "application/json",
            "X-Webhook-Timestamp": str(timestamp),
            "X-Webhook-Signature": signature_header,
            "X-Webhook-Event": delivery.event_type,
        }

        delivery.attempts += 1
        success = False
        error_msg = None

        try:
            async with httpx.AsyncClient(timeout=DELIVERY_TIMEOUT) as client:
                response = await client.post(endpoint.url, content=body, headers=headers)
                if 200 <= response.status_code < 300:
                    success = True
                else:
                    error_msg = f"HTTP {response.status_code}"
        except httpx.TimeoutException:
            error_msg = "delivery timeout"
        except Exception as e:
            error_msg = str(e)

        if success:
            delivery.status = WebhookDeliveryStatus.DELIVERED.value
            delivery.delivered_at = datetime.now(timezone.utc)
            logger.info(
                f"Webhook delivered: delivery={delivery.id} "
                f"event={delivery.event_type} endpoint={endpoint.url}"
            )
        else:
            delivery.last_error = error_msg
            if delivery.attempts >= MAX_ATTEMPTS:
                delivery.status = WebhookDeliveryStatus.EXHAUSTED.value
                logger.warning(
                    f"Webhook exhausted: delivery={delivery.id} "
                    f"event={delivery.event_type} error={error_msg}"
                )
            else:
                # Schedule next attempt with exponential backoff
                backoff = BACKOFF_SECONDS[min(delivery.attempts, len(BACKOFF_SECONDS) - 1)]
                delivery.next_attempt_at = datetime.now(timezone.utc) + timedelta(seconds=backoff)
                logger.info(
                    f"Webhook failed, retry in {backoff}s: "
                    f"delivery={delivery.id} error={error_msg}"
                )


worker = WebhookWorker()