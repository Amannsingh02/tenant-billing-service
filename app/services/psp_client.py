"""
PSP HTTP client.

Translates the PSP wire protocol into Python types.
Surfaces typed exceptions so payment_service can handle
business failures vs infrastructure failures differently.

Business failure (card_declined, insufficient_funds):
    PSPChargeResponse with status='failed' and a code.
    Invoice stays open. Customer can retry with a different card.

Infrastructure failure (timeout, 5xx):
    Raises PSPError.
    Attempt stays pending. Invoice stays open.
"""
import httpx

from app.config import get_settings
from app.exceptions import PSPError

settings = get_settings()


class PSPChargeResponse:
    def __init__(self, status: str, psp_ref: str | None, code: str | None):
        self.status = status      # "succeeded" or "failed"
        self.psp_ref = psp_ref    # filled on success
        self.code = code          # filled on business failure


async def charge(card_token: str, amount_cents: int, idempotency_key: str) -> PSPChargeResponse:
    """
    Call the PSP charge endpoint.

    Timeout is set explicitly — tok_timeout sleeps 30s on the PSP side.
    We cut it off at PSP_TIMEOUT_SECONDS (default 12) and surface PSPError.
    The caller leaves the attempt as pending and returns 202.
    """
    payload = {
        "card_token": card_token,
        "amount_cents": amount_cents,
        "idempotency_key": idempotency_key,
    }

    try:
        async with httpx.AsyncClient(timeout=settings.PSP_TIMEOUT_SECONDS) as client:
            response = await client.post(settings.PSP_URL, json=payload)
            response.raise_for_status()
            data = response.json()
            return PSPChargeResponse(
                status=data.get("status", "failed"),
                psp_ref=data.get("psp_ref"),
                code=data.get("code"),
            )

    except httpx.TimeoutException:
        raise PSPError("psp_timeout")

    except httpx.HTTPStatusError as e:
        raise PSPError(f"psp_unavailable: HTTP {e.response.status_code}")

    except httpx.RequestError as e:
        raise PSPError(f"psp_unavailable: {str(e)}")