"""FastAPI wrapper exposing the execution agent over HTTP (SCRUM-21).

One route, POST /chat. Each call builds a fresh AlpacaBroker/ExecutionDeps,
the agent itself is stateless across requests — multi-turn conversation
works by the caller round-tripping message history, not server-side
session state.

Auth: static API key via the X-API-Key header, checked against
COMPASS_API_KEY. This endpoint can place real (paper) trades, so it is
never exposed without auth.

Client-supplied history is a trust boundary: a client could fabricate
prior tool calls or inject a fake system prompt. Inbound history is
validated with ModelMessagesTypeAdapter, then run through
sanitize_messages() before being handed to the agent, per Pydantic
AI's documented guidance on untrusted history.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel
from pydantic_ai import ModelMessagesTypeAdapter
from pydantic_ai.messages import ModelMessage, sanitize_messages
from pydantic_core import to_jsonable_python

from compass.broker import AlpacaBroker
from compass.execution_agent import ExecutionDeps, execution_agent
from compass.models import OrderResult
from compass.tracing import enable_tracing, traced_run

load_dotenv()


@asynccontextmanager
async def lifespan(app: FastAPI):
    enable_tracing()
    yield


app = FastAPI(title="compass-execution-agent", lifespan=lifespan)


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    expected = os.environ["COMPASS_API_KEY"]
    if x_api_key != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


class ChatRequest(BaseModel):
    message: str
    history: list[dict] | None = None


class ChatResponse(BaseModel):
    response: OrderResult | str
    history: list[dict]


@app.post("/chat", response_model=ChatResponse, dependencies=[Depends(require_api_key)])
async def chat(request: ChatRequest) -> ChatResponse:
    message_history: list[ModelMessage] | None = None
    if request.history:
        raw = ModelMessagesTypeAdapter.validate_python(request.history)
        message_history = sanitize_messages(raw)

    deps = ExecutionDeps(broker=AlpacaBroker.from_env())
    with traced_run("execution_agent_api_call"):
        result = await execution_agent.run(
            request.message, deps=deps, message_history=message_history
        )

    return ChatResponse(
        response=result.output,
        history=to_jsonable_python(result.all_messages()),
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
