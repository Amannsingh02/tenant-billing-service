import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import CurrentBusiness
from app.schemas.webhook import WebhookEndpointCreate, WebhookEndpointCreated, WebhookEndpointRead
from app.services import webhook_service

router = APIRouter(prefix="/webhook_endpoints", tags=["webhooks"])


@router.post("", response_model=WebhookEndpointCreated, status_code=201)
async def create_webhook_endpoint(
    payload: WebhookEndpointCreate,
    business: CurrentBusiness,
    db: AsyncSession = Depends(get_db),
):
    endpoint, secret = await webhook_service.create_endpoint(db, business, payload.url)
    await db.commit()
    return WebhookEndpointCreated(
        id=endpoint.id,
        business_id=endpoint.business_id,
        url=endpoint.url,
        is_active=endpoint.is_active,
        created_at=endpoint.created_at,
        signing_secret=secret,
    )


@router.get("", response_model=list[WebhookEndpointRead])
async def list_webhook_endpoints(
    business: CurrentBusiness,
    db: AsyncSession = Depends(get_db),
):
    endpoints = await webhook_service.list_endpoints(db, business)
    return [WebhookEndpointRead.model_validate(e) for e in endpoints]


@router.delete("/{endpoint_id}", status_code=204)
async def deactivate_webhook_endpoint(
    endpoint_id: uuid.UUID,
    business: CurrentBusiness,
    db: AsyncSession = Depends(get_db),
):
    endpoint = await webhook_service.deactivate_endpoint(db, business, endpoint_id)
    if endpoint is None:
        raise HTTPException(status_code=404, detail="Webhook endpoint not found")
    await db.commit()