import uuid
from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.customer import PaginatedResponse  # reuse generic


class InvoiceLineItemCreate(BaseModel):
    description: str = Field(..., min_length=1, max_length=500)
    quantity: int = Field(..., gt=0)
    unit_amount_cents: int = Field(..., ge=0)


class InvoiceLineItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    description: str
    quantity: int
    unit_amount_cents: int
    amount_cents: int


class InvoiceCreate(BaseModel):
    customer_id: uuid.UUID
    due_date: Optional[date] = None
    line_items: List[InvoiceLineItemCreate] = Field(..., min_length=1)


class InvoiceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    customer_id: uuid.UUID
    state: str
    total_cents: int
    due_date: Optional[date]
    line_items: List[InvoiceLineItemRead]
    created_at: datetime
    updated_at: datetime