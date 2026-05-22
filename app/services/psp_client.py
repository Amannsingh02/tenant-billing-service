"""
Mock PSP HTTP client.

A thin wrapper around httpx. The PSP is treated as an external service —
configurable URL, explicit timeout, typed exceptions.

Three outcomes the payment service must distinguish:
 - Business outcome: PSP returned a clean 2xx response, succeeded or failed
   (card declined, insufficient funds). Modeled as PSPChargeResult.
 - Timeout: PSP didn't respond in time (tok_timeout sleeps 30s).
   Raises PSPTimeout. Payment stays pending.
 - Network/HTTP error: PSP returned 4xx/5xx, connection refused, etc
   (tok_network_error). Raises PSPNetworkError. Payment stays pending.
"""

from dataclasses import dataclass
from typing import Literal

import httpx

from app.config import get_settings


# ----------------------------------------------------------------------------
# Exception hierarchy
# ----------------------------------------------------------------------------

class PSPError(Exception):
    """Base class for PSP-related failures (NOT business failures)."""


class PSPTimeout(PSPError):
    """PSP did not respond before our configured timeout."""


class PSPNetworkError(PSPError):
    """PSP returned a non-2xx status or the connection failed."""


# ----------------------------------------------------------------------------
# Result type for clean responses
# ----------------------------------------------------------------------------

@dataclass(frozen=True)
class PSPChargeResult:
    """A clean response from the PSP — either succeeded or business-failed."""

    status: Literal["succeeded", "failed"]
    psp_ref: str | None = None
    code: str | None = None  # failure code on business failures (e.g. card_declined)


# ----------------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------------

async def charge(
    card_token: str,
    amount_cents: int,
    idempotency_key: str | None = None,
) -> PSPChargeResult:
    """
    Call the mock PSP to charge a card.

    Returns:
        PSPChargeResult — for any clean 2xx response (succeeded or failed).

    Raises:
        PSPTimeout — request timed out (tok_timeout case).
        PSPNetworkError — HTTP 4xx/5xx or connection failure (tok_network_error).
    """

    settings = get_settings()

    payload: dict = {
        "card_token": card_token,
        "amount_cents": amount_cents,
    }

    if idempotency_key is not None:
        payload["idempotency_key"] = idempotency_key

    try:
        async with httpx.AsyncClient(timeout=settings.PSP_TIMEOUT_SECONDS) as client:
            response = await client.post(settings.PSP_URL, json=payload)

    except httpx.TimeoutException as e:
        raise PSPTimeout(f"PSP did not respond within timeout: {e}") from e

    except httpx.RequestError as e:
        # Connection refused, DNS failure, broken socket, etc.
        raise PSPNetworkError(f"PSP connection failed: {e}") from e

    # Treat both 4xx and 5xx the same: we can't proceed and don't know the
    # transaction state. Leaves the payment attempt 'pending'.
    if response.status_code >= 400:
        raise PSPNetworkError(
            f"PSP returned HTTP {response.status_code}: {response.text[:200]}"
        )

    body = response.json()

    return PSPChargeResult(
        status=body["status"],
        psp_ref=body.get("psp_ref"),
        # The mock PSP returns "code" on failures. Tolerate either field name.
        code=body.get("code") or body.get("failure_code"),
    )