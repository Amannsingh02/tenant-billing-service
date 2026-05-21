import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import CurrentBusiness
from app.exceptions import ConflictError, NotFoundError
from app.schemas.customer import CustomerCreate, CustomerRead, PaginatedResponse
from app.services import customer_service

router = APIRouter(prefix="/customers", tags=["customers"])


@router.post("", response_model=CustomerRead, status_code=201)
async def create_customer(
    payload: CustomerCreate,
    business: CurrentBusiness,
    db: AsyncSession = Depends(get_db),
):
    try:
        customer = await customer_service.create_customer(
            db, business, payload.name, payload.email
        )
        await db.commit()
        return CustomerRead.model_validate(customer)
    except ConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.get("", response_model=PaginatedResponse[CustomerRead])
async def list_customers(
    business: CurrentBusiness,
    db: AsyncSession = Depends(get_db),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
):
    customers, total = await customer_service.list_customers(db, business, skip, limit)
    return PaginatedResponse(
        items=[CustomerRead.model_validate(c) for c in customers],
        total=total,
        skip=skip,
        limit=limit,
    )


@router.get("/{customer_id}", response_model=CustomerRead)
async def get_customer(
    customer_id: uuid.UUID,
    business: CurrentBusiness,
    db: AsyncSession = Depends(get_db),
):
    try:
        customer = await customer_service.get_customer(db, business, customer_id)
        return CustomerRead.model_validate(customer)
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Customer not found")