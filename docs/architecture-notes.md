# Architecture notes

Exploratory notes on the agent stack for Compass. Nothing here is locked in yet; scope is still being decided.

## Candidate stack: LangGraph + Pydantic AI

LangGraph would own orchestration: the graph of nodes, the shared state object flowing between them, conditional routing and branching, cycles, checkpointing, and human-in-the-loop interrupts. That's useful for a multi-step research flow (say, gather prices, then pull news, then check filings, then synthesize an explanation) where steps branch and state needs to persist across turns.

Pydantic AI would live inside individual nodes, replacing manually written "call the LLM, parse the JSON, hope it's well-formed" logic. We define an `Agent` with a typed output model (a Pydantic `BaseModel`) and typed tools (plain functions, schema-validated from type hints), and the framework handles the model call, tool dispatch, output validation, and automatic retry when the output doesn't validate.

### What a node might look like

```python
from pydantic_ai import Agent
from pydantic import BaseModel

class FilingSummary(BaseModel):
    ticker: str
    filing_type: str
    key_points: list[str]
    risk_flags: list[str]

filing_agent = Agent(
    "anthropic:claude-sonnet-5",
    output_type=FilingSummary,
    system_prompt="Summarize the filing objectively. Do not recommend buying or selling.",
)

def summarize_filing_node(state: GraphState) -> GraphState:
    result = filing_agent.run_sync(state["filing_text"])
    state["filing_summary"] = result.output  # validated, typed
    return state
```

LangGraph decides what happens next based on `state`. Pydantic AI guarantees `state["filing_summary"]` is actually a well-formed `FilingSummary`, not a string that might fail to parse downstream.

### Provider-agnostic model selection

Pydantic AI selects models through a string identifier, like `"anthropic:claude-sonnet-5"`, so swapping providers or models later doesn't require rewriting agent logic. That could matter if Compass ends up aggregating across multiple data or LLM providers.

## Open questions

Is this actually the stack we're building on, or just one option under consideration?

How does this interact with Compass's core constraint of explaining findings without recommending trades? Does that live in the system prompt, in an output schema that simply has no `recommendation` field, in a dedicated guardrail node, or some combination of all three?

What counts as a "node" here: one per data source (prices, news, filings), or a coarser split?

Does state need to survive across sessions, like a saved research thread a user comes back to, or is persistence only needed within a single conversation?



•⁠  ⁠prepare a set of 10 qna for you subagents
•⁠  ⁠build the subagents using pydantic ai agents library https://pydantic.dev/docs/ai/core-concepts/agent/
•⁠  ⁠(optional) learn skills implementation - best practices https://pydantic.dev/docs/ai/harness/skills/ + pydantic skills harness implementation https://pydantic.dev/docs/ai/harness/skills/
•⁠  ⁠learn skills implementation using pydantic ai capabilities https://pydantic.dev/docs/ai/capabilities/on-demand/ 
•⁠  ⁠set up a langfuse project for your respective subagent and test run, see the traces on your test cases
•⁠  ⁠online langfuse evaluation - use metrics that makes sense