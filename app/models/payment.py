import enum
import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import (
    BigInteger, CheckConstraint, DateTime,
    ForeignKey, String, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class PaymentStatus(str, enum.Enum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class PaymentAttempt(Base):
    __tablename__ = "payment_attempts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoices.id", ondelete="RESTRICT"), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=PaymentStatus.PENDING.value
    )
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    card_token: Mapped[str] = mapped_column(String(100), nullable=False)
    psp_ref: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    failure_code: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','succeeded','failed')",
            name="ck_payment_attempts_status",
        ),
        CheckConstraint("amount_cents > 0", name="ck_payment_attempts_amount_pos"),
        UniqueConstraint(
            "invoice_id", "idempotency_key",
            name="uq_payment_attempts_invoice_idem_key",
        ),
    )

    def __repr__(self) -> str:
        return f"<PaymentAttempt id={self.id} status={self.status} psp_ref={self.psp_ref}>"