"""
Customer service — DB logic only, no HTTP knowledge.

Every function takes `business: Business` explicitly.
Services flush but never commit — the router owns transaction boundaries.
Every query scopes by business.id — multi-tenant isolation enforced in code.
"""
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import ConflictError, NotFoundError
from app.models.business import Business
from app.models.customer import Customer


async def create_customer(
    session: AsyncSession,
    business: Business,
    name: str,
    email: str,
) -> Customer:
    customer = Customer(
        business_id=business.id,
        name=name,
        email=email,
    )
    session.add(customer)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise ConflictError(f"A customer with email '{email}' already exists")
    return customer


async def get_customer(
    session: AsyncSession,
    business: Business,
    customer_id,
) -> Customer:
    result = await session.execute(
        select(Customer).where(
            Customer.id == customer_id,
            Customer.business_id == business.id,
        )
    )
    customer = result.scalar_one_or_none()
    if customer is None:
        raise NotFoundError("Customer", customer_id)
    return customer


async def list_customers(
    session: AsyncSession,
    business: Business,
    skip: int = 0,
    limit: int = 20,
) -> tuple[list[Customer], int]:
    # total count
    count_result = await session.execute(
        select(func.count()).where(Customer.business_id == business.id)
    )
    total = count_result.scalar_one()

    # paginated rows
    result = await session.execute(
        select(Customer)
        .where(Customer.business_id == business.id)
        .order_by(Customer.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    customers = list(result.scalars().all())
    return customers, total