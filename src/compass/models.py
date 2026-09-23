"""Shared typed outputs for Compass agents."""

from pydantic import BaseModel


class OrderResult(BaseModel):
    """Outcome of placing, replacing, or looking up an order.

    No recommendation/suggestion field by design — execution tools report
    what happened, they never advise what to do next.
    """

    order_id: str
    status: str
    symbol: str
    qty: float | None
    notional: float | None
    account_mode: str
