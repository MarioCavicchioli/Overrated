"""Orchestrator API and watcher.

    uvicorn orchestrator.app:app --port 8200

Endpoints are for the app's server side and need ORCHESTRATOR_API_TOKEN as a
bearer token. The orchestrator refuses to serve them if the token isn't set.
"""

from __future__ import annotations

import asyncio
import hmac
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from orchestrator.cases import ApprovalError, approve_case, risk_line
from orchestrator.config import load_env
from orchestrator.pipeline import Pipeline
from orchestrator.store import Store, get_store

load_env()


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.store = get_store()
    app.state.pipeline = Pipeline(app.state.store)
    task = None
    if os.getenv("WATCH_DISABLED") != "1":
        task = asyncio.create_task(app.state.pipeline.watch())
    yield
    if task:
        task.cancel()


app = FastAPI(title="Overrated orchestrator", lifespan=lifespan)


def store() -> Store:
    return app.state.store


def require_token(authorization: str = Header(default="")) -> None:
    expected = os.getenv("ORCHESTRATOR_API_TOKEN")
    if not expected:
        raise HTTPException(503, "ORCHESTRATOR_API_TOKEN is not set")
    given = authorization.removeprefix("Bearer ").strip()
    if not hmac.compare_digest(given.encode(), expected.encode()):
        raise HTTPException(401, "bad token")


class Approval(BaseModel):
    account_reference: str = Field(default="", max_length=64,
                                   description="Rates bill account reference: proof of authority")
    facts_confirmed: bool = Field(default=False,
                                  description="The owner confirms the facts in the pack are true")


def _case_view(case: dict) -> dict:
    return {**case, "risk": risk_line(case)}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/cases/{case_id}", dependencies=[Depends(require_token)])
def get_case(case_id: str):
    case = store().get_case(case_id)
    if case is None:
        raise HTTPException(404, f"no case {case_id}")
    return _case_view(case)


@app.post("/cases/{case_id}/approve", dependencies=[Depends(require_token)])
def approve(case_id: str, body: Approval):
    try:
        case = approve_case(store(), case_id, body.account_reference, body.facts_confirmed)
    except ApprovalError as e:
        raise HTTPException(e.status, e.detail)
    return _case_view(case)
