"""FastAPI service exposing the escalation pipeline behind JWT-authenticated, role-gated routes.

    POST /token              — dev login, issues a JWT (see auth.py)
    POST /escalations        — submit an escalation manually (role: operator)
    GET  /audit/{id}         — read an escalation's current state + full history (either role)
    POST /approve            — resolve the human-approval gate (role: approver)

Run with: uvicorn api:app --port 8080
(port 8080, not 8000 — DynamoDB Local's default port is 8000; see README)

When this service is running, use it (not `main.py run`/`review`) as the sole process touching
CHECKPOINT_DB_PATH — see pipeline.py's module docstring for why.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel

import db
import gmail_client
import pipeline
from auth import TokenUser, create_access_token, authenticate_user, get_current_user, require_role
from config import CONFIG

logger = logging.getLogger(__name__)

_poller_task: asyncio.Task | None = None


async def _poller_loop() -> None:
    service = gmail_client.get_gmail_service()
    while True:
        try:
            await asyncio.to_thread(pipeline.poll_once, service)
        except Exception:
            logger.exception("Background poll cycle failed; will retry next cycle")
        await asyncio.sleep(CONFIG.gmail_poll_interval_minutes * 60)


@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI):
    global _poller_task
    db.init_db()
    if CONFIG.enable_poller:
        _poller_task = asyncio.create_task(_poller_loop())
        logger.info("Background Gmail poller started (ENABLE_POLLER=true).")
    else:
        logger.info("Background Gmail poller disabled (ENABLE_POLLER=false).")

    yield

    if _poller_task is not None:
        _poller_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _poller_task


app = FastAPI(title="CEO Escalation Handling API", lifespan=lifespan)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


@app.post("/token", response_model=TokenResponse)
async def login(form_data: OAuth2PasswordRequestForm = Depends()) -> TokenResponse:
    user = authenticate_user(form_data.username, form_data.password)
    if user is None:
        raise HTTPException(status_code=401, detail="Incorrect username or password")
    token = create_access_token(user.username, user.role)
    return TokenResponse(access_token=token)


class EscalationSubmission(BaseModel):
    sender: str
    subject: str
    raw_body: str
    gmail_message_id: str | None = None
    gmail_thread_id: str | None = None
    received_at: str | None = None


class EscalationCreated(BaseModel):
    escalation_id: str


@app.post("/escalations", response_model=EscalationCreated, dependencies=[Depends(require_role("operator"))])
async def create_escalation(payload: EscalationSubmission) -> EscalationCreated:
    escalation_id = await asyncio.to_thread(pipeline.submit_escalation, payload.model_dump())
    return EscalationCreated(escalation_id=escalation_id)


class AuditResponse(BaseModel):
    current: dict | None
    history: list[dict]


@app.get("/audit/{escalation_id}", response_model=AuditResponse)
async def get_audit(escalation_id: str, _user: TokenUser = Depends(get_current_user)) -> AuditResponse:
    current = await asyncio.to_thread(db.get_escalation, escalation_id)
    if current is None:
        raise HTTPException(status_code=404, detail=f"No escalation with id={escalation_id!r}")
    history = await asyncio.to_thread(db.get_escalation_history, escalation_id)
    return AuditResponse(current=current, history=history)


class ApprovalDecision(BaseModel):
    escalation_id: str
    decision: Literal["approved", "rejected"]
    final_team: str | None = None
    final_action: str | None = None
    notes: str | None = None


@app.post("/approve", dependencies=[Depends(require_role("approver"))])
async def approve(payload: ApprovalDecision) -> dict:
    result = await asyncio.to_thread(
        pipeline.resolve_approval,
        payload.escalation_id,
        {
            "decision": payload.decision,
            "final_team": payload.final_team,
            "final_action": payload.final_action,
            "notes": payload.notes,
        },
    )
    if result is None:
        raise HTTPException(status_code=404, detail=f"No escalation with id={payload.escalation_id!r}")
    return result
