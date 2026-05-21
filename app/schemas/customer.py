import uuid
from datetime import datetime
from typing import Generic, List, TypeVar

from pydantic import BaseModel, ConfigDict, EmailStr, Field

T = TypeVar("T")


class PaginatedResponse(BaseModel, Generic[T]):
    items: List[T]
    total: int
    skip: int
    limit: int


class CustomerCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    email: EmailStr = Field(..., max_length=320)


class CustomerRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    email: str
    created_at: datetime
    updated_at: datetime