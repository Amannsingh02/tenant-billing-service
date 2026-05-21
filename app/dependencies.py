from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.business import ApiKey, Business
from app.utils.api_keys import hash_api_key
from fastapi import HTTPException

# auto_error=False means we handle the 401 ourselves — consistent error format
# across all failure modes. With auto_error=True, FastAPI returns its own 403
# body that's inconsistent with the rest of the API.
_bearer = HTTPBearer(auto_error=False)

_UNAUTHORIZED = HTTPException(
    status_code=401,
    detail="Invalid API key",
    headers={"WWW-Authenticate": "Bearer"},
)


async def get_current_business(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    db: AsyncSession = Depends(get_db),
) -> Business:
    """
    FastAPI dependency — resolves the authenticated Business from the
    Authorization: Bearer <api_key> header.

    All failure modes return the same 401 with "Invalid API key":
    - Missing header
    - Wrong scheme (Basic, Digest, etc.)
    - Empty token
    - Unknown key
    - Revoked / inactive key
    - Business deleted (edge case)

    One message for all failures. Attackers learn nothing from probing.
    This is the OWASP-recommended approach for API key auth.
    """
    # Step 1: header present and Bearer scheme used?
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _UNAUTHORIZED

    plaintext = credentials.credentials.strip()
    if not plaintext:
        raise _UNAUTHORIZED

    # Step 2: hash the key and look it up
    # Unsalted SHA-256 → O(log n) index lookup on key_hash (UNIQUE index).
    # Salted hashes would require iterating all keys — unworkable at auth time.
    key_hash = hash_api_key(plaintext)

    result = await db.execute(
        select(ApiKey).where(
            ApiKey.key_hash == key_hash,
            ApiKey.is_active.is_(True),
        )
    )
    api_key = result.scalar_one_or_none()

    if api_key is None:
        raise _UNAUTHORIZED

    # Step 3: load the parent business (explicit query, no ORM relationship)
    # We deliberately have no relationship() on ApiKey — async lazy-loading
    # is a footgun. The one place we need (api_key + business) together is
    # here, so an explicit second query is clearer and doesn't couple the
    # schema to one use case.
    result = await db.execute(
        select(Business).where(Business.id == api_key.business_id)
    )
    business = result.scalar_one_or_none()

    if business is None:
        # Key exists but business was deleted — edge case, same 401
        raise _UNAUTHORIZED

    return business


# Annotated type alias — routes use this instead of the full Depends() call.
# Usage in a router:
#   async def my_route(business: CurrentBusiness, ...):
CurrentBusiness = Annotated[Business, Depends(get_current_business)]