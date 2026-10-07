# compass

An agentic assistant for market research which aggregates live prices, news, and filings across multiple providers, and explains what it finds without telling you what to trade.

## Status

Early stage. Provider research (price data, news/sentiment, trade execution) is tracked in Jira under `SCRUM-6`. The trade execution subagent is the first one built out, and is now deployed and callable remotely — see `src/compass/` and the docs below.

**Live endpoint:** deployed to Cloud Run (see [docs/api-usage.md](docs/api-usage.md) for how to look up the URL and call it)

## Docs

- [docs/architecture.md](docs/architecture.md) — how the agent, broker, API, tracing, and evals fit together
- [docs/api-usage.md](docs/api-usage.md) — how to call the deployed `/chat` endpoint
- [docs/deployment.md](docs/deployment.md) — GCP/Cloud Run setup, secrets, CI/CD, how to reproduce it
- [docs/evals.md](docs/evals.md) — what the Langfuse evals cover and current reliability findings
- [docs/architecture-notes.md](docs/architecture-notes.md) — original pre-code planning notes (mostly superseded)

## Setup

```bash
uv sync
cp .env.example .env  # fill in real values
```

Required in `.env`:

- `GROQ_API_KEY`: free tier, used via Groq's OpenAI-compatible endpoint
- `ALPACA_PAPER_KEY_ID` / `ALPACA_PAPER_SECRET_KEY`: Alpaca paper trading account
- `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_BASE_URL`: free tier tracing
- `COMPASS_API_KEY`: required by clients of the FastAPI `/chat` endpoint

## Execution agent

Places and manages orders on the user's Alpaca paper account. Tools are thin wrappers over `AlpacaBroker` (`src/compass/broker.py`), verified independently against the real Alpaca API. See [docs/architecture.md](docs/architecture.md) for the full design, including the guardrails (order size limits, explicit-ID requirement for status/cancel/replace).

```bash
uv run python -m compass.execution_agent   # interactive CLI
```

Paper-vs-live is enforced in `AlpacaBroker` itself, not exposed to the model as something it could influence.

### Running it over HTTP

```bash
uv run uvicorn compass.api:app --reload   # local dev server
```

See [docs/api-usage.md](docs/api-usage.md) for the request/response shape and auth.

### Evals

```bash
uv run python -m compass.eval_execution_agent        # tool choice
uv run python -m compass.eval_execution_agent_args    # tool choice + argument correctness
```

Both run against Langfuse datasets and the real paper account. See [docs/evals.md](docs/evals.md) for current reliability findings and what's covered.

### Linting

```bash
uv run ruff check .
```

Same check CI runs on every push/PR to `main`.

## Deployment

Live on Cloud Run, auto-deployed on push to `main` via Cloud Build, gated by GitHub Actions CI. See [docs/deployment.md](docs/deployment.md) for the full setup, secrets, and IAM configuration.

## Project layout

```text
src/compass/
  broker.py                      AlpacaBroker, Alpaca Trading + Market Data API wrapper
  models.py                      Shared typed outputs (OrderResult)
  execution_agent.py             Trade execution Pydantic AI agent
  api.py                         FastAPI wrapper (/chat, /health)
  tracing.py                     Langfuse/OTel tracing setup
  eval_execution_agent.py        Tool-choice eval
  eval_execution_agent_args.py   Tool-argument eval
docs/
  architecture.md                Current design
  api-usage.md                   How to call the deployed endpoint
  deployment.md                  GCP/Cloud Run/CI setup
  evals.md                       Eval coverage and findings
  architecture-notes.md          Original pre-code planning notes
.github/workflows/ci.yml         Lint + import check, required on main
Dockerfile                       Container build, listens on $PORT
```

## Next steps

- Positions visibility (`get_all_positions`) — SCRUM-18
- Automated test suite for `AlpacaBroker` — SCRUM-20
- Rate limiting at the API layer — SCRUM-22, SCRUM-23
- Post-deploy smoke test — SCRUM-25
- Multi-agent orchestration, once teammates' subagents exist — see [docs/architecture-notes.md](docs/architecture-notes.md)
