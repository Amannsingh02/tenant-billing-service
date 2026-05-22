"""
HMAC-SHA256 webhook signing utilities.

What gets signed: f"{timestamp}.{body}"
Why include timestamp: replay protection. An attacker who captures a valid
request can't re-send it after the freshness window expires.

Header format (Stripe-style):
    X-Webhook-Signature: t=<unix_timestamp>,v1=<hmac_hex>
    X-Webhook-Timestamp: <unix_timestamp>

The receiver:
1. Extracts t from the signature header
2. Re-computes HMAC over f"{t}.{body}"
3. Constant-time compares with v1
4. Rejects if t is older than 5 minutes
"""
import hashlib
import hmac
import time


def sign_payload(secret: str, body: str, timestamp: int | None = None) -> tuple[str, int]:
    """
    Sign a webhook payload.

    Returns: (signature_header_value, timestamp)
    e.g. ("t=1716508800,v1=abc123...", 1716508800)
    """
    if timestamp is None:
        timestamp = int(time.time())

    signed_content = f"{timestamp}.{body}"
    mac = hmac.new(
        secret.encode("utf-8"),
        signed_content.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    header_value = f"t={timestamp},v1={mac}"
    return header_value, timestamp


def verify_signature(
    secret: str,
    body: str,
    signature_header: str,
    max_age_seconds: int = 300,
) -> bool:
    """
    Verify a webhook signature.

    Returns True if valid and fresh, False otherwise.
    Uses constant-time comparison to prevent timing attacks.
    """
    try:
        parts = dict(item.split("=", 1) for item in signature_header.split(","))
        timestamp = int(parts["t"])
        received_sig = parts["v1"]
    except (KeyError, ValueError):
        return False

    # Freshness check — reject replays older than max_age_seconds
    if abs(int(time.time()) - timestamp) > max_age_seconds:
        return False

    signed_content = f"{timestamp}.{body}"
    expected_sig = hmac.new(
        secret.encode("utf-8"),
        signed_content.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected_sig, received_sig)