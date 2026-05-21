import asyncio
import uuid
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="mock-psp")


class ChargeRequest(BaseModel):
    card_token: str
    amount_cents: int
    idempotency_key: str | None = None


class ChargeResponse(BaseModel):
    status: str
    psp_ref: str | None = None
    code: str | None = None


@app.get("/health")
async def health():
    return {"status": "ok", "service": "mock_psp"}


@app.post("/charge", response_model=ChargeResponse)
async def charge(req: ChargeRequest):
    token = req.card_token

    if token == "tok_success":
        await asyncio.sleep(0.1)
        return ChargeResponse(status="succeeded", psp_ref=str(uuid.uuid4()))

    if token == "tok_insufficient_funds":
        await asyncio.sleep(0.1)
        return ChargeResponse(status="failed", code="insufficient_funds")

    if token == "tok_card_declined":
        await asyncio.sleep(0.1)
        return ChargeResponse(status="failed", code="card_declined")

    if token == "tok_timeout":
        await asyncio.sleep(30)
        return ChargeResponse(status="succeeded", psp_ref=str(uuid.uuid4()))

    if token == "tok_network_error":
        raise HTTPException(status_code=500, detail="psp_unavailable")

    raise HTTPException(status_code=400, detail=f"unknown token: {token}")