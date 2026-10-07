# API usage

How to call the deployed execution agent. See
[architecture.md](architecture.md) for how `api.py` is built.

## Base URL

Not published here, Look it up with:

```bash
gcloud run services describe compass-execution-agent \
  --project=compass-execution-agent --region=us-central1 \
  --format="value(status.url)"
```

Requires `gcloud` access to the `compass-execution-agent` GCP project.
The URL is stable unless the service is recreated, but always confirm
rather than hardcode it elsewhere.

## Authentication

Every `/chat` request needs an `X-API-Key` header set to the
`COMPASS_API_KEY` secret value. Requests without it, or with the wrong
value, get `401`.

Get the current key (requires `gcloud` access to the
`compass-execution-agent` GCP project):

```bash
gcloud secrets versions access latest \
  --secret=COMPASS_API_KEY --project=compass-execution-agent
```

Don't print this key into shared logs, chat transcripts, or commit it
anywhere, treat it like any other credential.

## Endpoints

The examples below assume you've set:

```bash
SERVICE_URL=$(gcloud run services describe compass-execution-agent \
  --project=compass-execution-agent --region=us-central1 \
  --format="value(status.url)")
```

### `GET /health`

No auth required. Returns `{"status": "ok"}` if the service is up.

```bash
curl "$SERVICE_URL/health"
```

### `POST /chat`

The real endpoint. Request body:

```json
{
  "message": "What is the last price of AAPL?",
  "history": null
}
```

- `message` (string, required) — the user's message.
- `history` (array, optional) — prior conversation history, for
  multi-turn continuity. Omit or pass `null` for a fresh conversation.

Response body:

```json
{
  "response": "The most recent last-trade price for AAPL is $329.58.",
  "history": [ ... ]
}
```

- `response`L either a plain string, or a structured `OrderResult`
  object (`order_id`, `status`, `symbol`, `qty`, `notional`,
  `account_mode`) when the agent took an action like placing an order.
- `history`L the full updated message history. Pass this back as
  `history` in your next request to continue the conversation.

### Example: single-turn

```bash
curl -X POST "$SERVICE_URL/chat" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <your COMPASS_API_KEY>" \
  -d '{"message": "What is the last price of AAPL?"}'
```

### Example: multi-turn

```bash
# First turn, save the response
RESPONSE=$(curl -s -X POST "$SERVICE_URL/chat" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <your COMPASS_API_KEY>" \
  -d '{"message": "What is the last price of AAPL?"}')

# Extract history, pass it into the next call
HISTORY=$(echo "$RESPONSE" | python3 -c "import json,sys; print(json.dumps(json.load(sys.stdin)['history']))")

curl -s -X POST "$SERVICE_URL/chat" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <your COMPASS_API_KEY>" \
  -d "{\"message\": \"What was the price I just asked about?\", \"history\": $HISTORY}"
```

## What this agent will and won't do

- **It only executes what you explicitly ask.** It never recommends
  what to buy, sell, or hold.
- **It requires an explicit order ID** for checking status, cancelling,
  or replacing an order. "Cancel my pending TSLA order" won't work,  it
  will ask for the order ID, or offer to pull your trade history so you
  can find it. See [architecture.md](architecture.md#behavior-asking-vs-guessing)
  for why.
- **Orders are capped** at 100 shares or $10,000 notional per order. A
  larger request gets refused with an explanation, not silently
  reduced.
- **Everything happens on the Alpaca paper account.** No real money is
  ever at risk. Every response that takes an action states this
  explicitly.

## Known limitations

- Running on Groq's free tier, which has a tight rate limit (8,000
  tokens/minute). Heavy or rapid use can produce `429`/`503`-style
  failures from the underlying model call. Not yet handled gracefully
  at the API layer (tracked as SCRUM-22).
- No rate limiting of our own yet,  nothing stops one caller from
  exhausting the shared Groq budget for everyone (tracked as SCRUM-23).
- Reliability on ambiguous queries without complete information varies
  — see [evals.md](evals.md) for current measured numbers.
