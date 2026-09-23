"""Langfuse tracing setup, shared across all Compass agents.

Pydantic AI has no dedicated Langfuse plugin — Langfuse consumes the
OpenTelemetry spans Pydantic AI already emits when instrumented.
"""

from __future__ import annotations

from contextlib import contextmanager

from langfuse import get_client
from opentelemetry import trace
from pydantic_ai.agent import Agent

_tracer = trace.get_tracer("compass")


def enable_tracing() -> None:
    """Turn on OTel instrumentation for every Pydantic AI agent in the
    process, and verify the Langfuse client can authenticate.

    Call once, early, before constructing any Agent, requires
    LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, and LANGFUSE_BASE_URL to
    be set (e.g. via .env).
    """
    Agent.instrument_all()
    client = get_client()
    client.auth_check()


@contextmanager
def traced_run(name: str):
    """Wrap an agent run in a root span so the model call and every
    tool call it makes land in ONE trace, not one trace each.

    Without an active span, Pydantic AI's model/tool spans have no
    parent to nest under — Langfuse's get_current_trace_id() returns
    None and each call fragments into its own trace. Usage:

        with traced_run("execution_agent_run"):
            result = execution_agent.run_sync(query, deps=deps)
    """
    with _tracer.start_as_current_span(name):
        yield
