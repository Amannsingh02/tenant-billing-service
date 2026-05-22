import uuid

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import CurrentBusiness
from app.exceptions import (
    IdempotencyConflict,
    InvalidStateTransition,
    MissingIdempotencyKey,
    NotFoundError,
)
from app.models.payment import PaymentStatus
from app.schemas.payment import PaymentAttemptRead, PaymentRequest
from app.services import payment_service

router = APIRouter(prefix="/invoices", tags=["payments"])


@router.post("/{invoice_id}/pay")
async def pay_invoice(
    invoice_id: uuid.UUID,
    payload: PaymentRequest,
    business: CurrentBusiness,
    db: AsyncSession = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    if not idempotency_key:
        raise HTTPException(status_code=422, detail="Idempotency-Key header is required")

    try:
        attempt, is_cached = await payment_service.process_payment(
            session=db,
            business=business,
            invoice_id=invoice_id,
            card_token=payload.card_token,
            idempotency_key=idempotency_key,
        )

    except NotFoundError:
        raise HTTPException(status_code=404, detail="Invoice not found")

    except InvalidStateTransition as e:
        raise HTTPException(status_code=422, detail=str(e))

    except IdempotencyConflict as e:
        raise HTTPException(status_code=409, detail=str(e))

    # Map attempt status to HTTP status code
    if attempt.status == PaymentStatus.SUCCEEDED.value:
        status_code = 200
    elif attempt.status == PaymentStatus.FAILED.value:
        status_code = 402  # Payment Required
    else:
        status_code = 202  # Accepted (pending)

    from fastapi.responses import JSONResponse
    return JSONResponse(
        status_code=status_code,
        content=PaymentAttemptRead.model_validate(attempt).model_dump(mode="json"),
    )