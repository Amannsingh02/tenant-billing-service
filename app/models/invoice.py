import enum
import uuid
from datetime import date, datetime
from sqlalchemy import (
    BigInteger, CheckConstraint, Date, DateTime,
    ForeignKey, Index, Integer, String, func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class InvoiceState(str, enum.Enum):
    DRAFT = "draft"
    OPEN = "open"
    PAID = "paid"
    VOID = "void"
    UNCOLLECTIBLE = "uncollectible"


TERMINAL_STATES: frozenset = frozenset({
    InvoiceState.PAID,
    InvoiceState.VOID,
    InvoiceState.UNCOLLECTIBLE,
})


class Invoice(Base):
    __tablename__ = "invoices"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    state: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=InvoiceState.DRAFT.value
    )
    total_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "state IN ('draft','open','paid','void','uncollectible')",
            name="ck_invoices_state",
        ),
        CheckConstraint("total_cents >= 0", name="ck_invoices_total_nonneg"),
        Index("ix_invoices_business_state", "business_id", "state"),
        Index("ix_invoices_business_created", "business_id", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<Invoice id={self.id} state={self.state} total_cents={self.total_cents}>"


class InvoiceLineItem(Base):
    __tablename__ = "invoice_line_items"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_line_items_qty_positive"),
        CheckConstraint("unit_amount_cents >= 0", name="ck_line_items_unit_amount_nonneg"),
        CheckConstraint("amount_cents = quantity * unit_amount_cents", name="ck_line_items_amount_matches"),
    )

    def __repr__(self) -> str:
        return f"<LineItem desc={self.description!r} qty={self.quantity} amount={self.amount_cents}>"