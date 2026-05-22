"""
Invoice service — DB logic only, no HTTP knowledge.

Services flush but never commit.
Every query scopes by business.id — multi-tenant isolation enforced in code.
"""
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.exceptions import NotFoundError
from app.models.business import Business
from app.models.customer import Customer
from app.models.invoice import Invoice, InvoiceLineItem, InvoiceState
from app.schemas.invoice import InvoiceCreate
from app.services.invoice_state import assert_transition_allowed


async def create_invoice(
    session: AsyncSession,
    business: Business,
    payload: InvoiceCreate,
) -> Invoice:
    result = await session.execute(
        select(Customer).where(
            Customer.id == payload.customer_id,
            Customer.business_id == business.id,
        )
    )
    customer = result.scalar_one_or_none()
    if customer is None:
        raise NotFoundError("Customer")

    total_cents = sum(
        li.quantity * li.unit_amount_cents for li in payload.line_items
    )

    invoice = Invoice(
        business_id=business.id,
        customer_id=payload.customer_id,
        state=InvoiceState.DRAFT.value,
        total_cents=total_cents,
        due_date=payload.due_date,
    )
    session.add(invoice)
    await session.flush()

    for li in payload.line_items:
        amount_cents = li.quantity * li.unit_amount_cents
        line_item = InvoiceLineItem(
            invoice_id=invoice.id,
            description=li.description,
            quantity=li.quantity,
            unit_amount_cents=li.unit_amount_cents,
            amount_cents=amount_cents,
        )
        session.add(line_item)

    await session.flush()

    # Enqueue webhook in the SAME transaction — rolls back if caller rolls back
    from app.services import webhook_service
    await webhook_service.enqueue_event(
        db=session,
        business_id=business.id,
        event_type="invoice.created",
        payload={
            "id": str(invoice.id),
            "customer_id": str(invoice.customer_id),
            "state": invoice.state,
            "total_cents": invoice.total_cents,
        },
    )

    result = await session.execute(
        select(Invoice)
        .options(selectinload(Invoice.line_items))
        .where(Invoice.id == invoice.id)
    )
    return result.scalar_one()


async def get_invoice(
    session: AsyncSession,
    business: Business,
    invoice_id: uuid.UUID,
) -> Invoice:
    result = await session.execute(
        select(Invoice)
        .options(selectinload(Invoice.line_items))
        .where(
            Invoice.id == invoice_id,
            Invoice.business_id == business.id,
        )
    )
    invoice = result.scalar_one_or_none()
    if invoice is None:
        raise NotFoundError("Invoice")
    return invoice


async def list_invoices(
    session: AsyncSession,
    business: Business,
    state: str | None = None,
    skip: int = 0,
    limit: int = 20,
) -> tuple[list[Invoice], int]:
    base_query = select(Invoice).where(Invoice.business_id == business.id)

    if state:
        base_query = base_query.where(Invoice.state == state)

    count_result = await session.execute(
        select(func.count()).select_from(base_query.subquery())
    )
    total = count_result.scalar_one()

    result = await session.execute(
        base_query
        .options(selectinload(Invoice.line_items))
        .order_by(Invoice.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    invoices = list(result.scalars().all())
    return invoices, total


async def transition_invoice_state(
    session: AsyncSession,
    business: Business,
    invoice_id: uuid.UUID,
    target_state: InvoiceState,
) -> Invoice:
    invoice = await get_invoice(session, business, invoice_id)
    assert_transition_allowed(invoice.state, target_state)
    invoice.state = target_state.value
    await session.flush()

    result = await session.execute(
        select(Invoice)
        .options(selectinload(Invoice.line_items))
        .where(Invoice.id == invoice.id)
    )
    return result.scalar_one()