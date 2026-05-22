import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import CurrentBusiness
from app.exceptions import ConflictError, InvalidStateTransition, NotFoundError
from app.models.invoice import InvoiceState
from app.schemas.customer import PaginatedResponse
from app.schemas.invoice import InvoiceCreate, InvoiceRead
from app.services import invoice_service

router = APIRouter(prefix="/invoices", tags=["invoices"])


def _invoice_read(invoice) -> InvoiceRead:
    return InvoiceRead.model_validate(invoice)


@router.post("", response_model=InvoiceRead, status_code=201)
async def create_invoice(
    payload: InvoiceCreate,
    business: CurrentBusiness,
    db: AsyncSession = Depends(get_db),
):
    try:
        invoice = await invoice_service.create_invoice(db, business, payload)
        await db.commit()
        return _invoice_read(invoice)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("", response_model=PaginatedResponse[InvoiceRead])
async def list_invoices(
    business: CurrentBusiness,
    db: AsyncSession = Depends(get_db),
    state: Optional[str] = Query(default=None),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
):
    # Validate state filter if provided
    if state and state not in [s.value for s in InvoiceState]:
        raise HTTPException(status_code=422, detail=f"Invalid state: {state}")

    invoices, total = await invoice_service.list_invoices(db, business, state, skip, limit)
    return PaginatedResponse(
        items=[_invoice_read(i) for i in invoices],
        total=total,
        skip=skip,
        limit=limit,
    )


@router.get("/{invoice_id}", response_model=InvoiceRead)
async def get_invoice(
    invoice_id: uuid.UUID,
    business: CurrentBusiness,
    db: AsyncSession = Depends(get_db),
):
    try:
        invoice = await invoice_service.get_invoice(db, business, invoice_id)
        return _invoice_read(invoice)
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Invoice not found")


async def _transition(invoice_id, target_state, business, db) -> InvoiceRead:
    try:
        invoice = await invoice_service.transition_invoice_state(
            db, business, invoice_id, target_state
        )
        await db.commit()
        return _invoice_read(invoice)
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Invoice not found")
    except InvalidStateTransition as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.post("/{invoice_id}/finalize", response_model=InvoiceRead)
async def finalize_invoice(
    invoice_id: uuid.UUID,
    business: CurrentBusiness,
    db: AsyncSession = Depends(get_db),
):
    return await _transition(invoice_id, InvoiceState.OPEN, business, db)


@router.post("/{invoice_id}/void", response_model=InvoiceRead)
async def void_invoice(
    invoice_id: uuid.UUID,
    business: CurrentBusiness,
    db: AsyncSession = Depends(get_db),
):
    return await _transition(invoice_id, InvoiceState.VOID, business, db)


@router.post("/{invoice_id}/uncollectible", response_model=InvoiceRead)
async def mark_uncollectible(
    invoice_id: uuid.UUID,
    business: CurrentBusiness,
    db: AsyncSession = Depends(get_db),
):
    return await _transition(invoice_id, InvoiceState.UNCOLLECTIBLE, business, db)