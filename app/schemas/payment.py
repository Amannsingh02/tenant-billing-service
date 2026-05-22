import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class PaymentRequest(BaseModel):
    card_token: str


class PaymentAttemptRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    invoice_id: uuid.UUID
    status: str
    amount_cents: int
    card_token: str
    psp_ref: Optional[str]
    failure_code: Optional[str]
    idempotency_key: str
    created_at: datetime
    updated_at: datetime