import hashlib
import hmac
import secrets
from dataclasses import dataclass

from app.config import get_settings

settings = get_settings()

PREFIX = "sk_live_"
PREFIX_DISPLAY_LENGTH = 12  # chars of plaintext safe to store/log


@dataclass
class GeneratedApiKey:
    plaintext: str   # shown to user ONCE, never stored
    key_hash: str    # SHA-256 hex, stored in DB
    key_prefix: str  # first 12 chars of plaintext, stored in DB for display


def generate_api_key() -> GeneratedApiKey:
    """
    Generate a new API key.

    Format: sk_live_<32 chars of base64url> (~192 bits entropy)
    Only the hash and display prefix are stored. The plaintext is
    returned once and never persisted.

    Why SHA-256 and not bcrypt:
    API keys are random 256-bit secrets — already maximum entropy.
    Dictionary attacks are impossible. Fast hashing is correct here
    because auth runs on every request. Bcrypt is for low-entropy
    human-chosen passwords.
    """
    random_part = secrets.token_urlsafe(32)  # 32 bytes → 43 base64url chars
    plaintext = f"{PREFIX}{random_part}"
    key_hash = _sha256_hex(plaintext)
    key_prefix = plaintext[:PREFIX_DISPLAY_LENGTH]

    return GeneratedApiKey(
        plaintext=plaintext,
        key_hash=key_hash,
        key_prefix=key_prefix,
    )


def hash_api_key(plaintext: str) -> str:
    """
    Hash a plaintext API key for DB lookup.
    Called on every authenticated request — must be fast.
    """
    return _sha256_hex(plaintext)


def verify_api_key(plaintext: str, stored_hash: str) -> bool:
    """
    Constant-time comparison of a plaintext key against its stored hash.

    Why hmac.compare_digest and not ==:
    Python's == short-circuits on the first different character, leaking
    timing info. hmac.compare_digest always takes the same time regardless
    of where strings differ. Paranoid for hashed keys (hash is one-way),
    but correct by reflex — costs nothing, signals security awareness.
    """
    candidate_hash = _sha256_hex(plaintext)
    return hmac.compare_digest(candidate_hash, stored_hash)


def _sha256_hex(value: str) -> str:
    """SHA-256 hex digest of a UTF-8 string."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()