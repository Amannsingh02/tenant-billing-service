import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, HttpUrl


class WebhookEndpointCreate(BaseModel):
    url: str  # plain str so we can store as-is without Pydantic URL mangling


class WebhookEndpointRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    business_id: uuid.UUID
    url: str
    is_active: bool
    created_at: datetime
    # signing_secret is NOT included — shown only at creation time


class WebhookEndpointCreated(WebhookEndpointRead):
    """Returned only on POST — includes the signing secret (shown once)."""
    signing_secret: str