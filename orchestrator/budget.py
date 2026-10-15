"""Per-session budget: dollar cap, token meter and turn cap.

SESSION_TOKEN_BUDGET is in tokens, but Managed Agents caps spend in US cents.
Every session gets:
- a hard platform cap (the dollar cap): either an explicit dollar amount, or
  the token budget priced as if every token were output (the dearest kind), so
  the platform never stops a session before we would;
- a client-side meter that counts real tokens from span.model_request_end and
  interrupts the session when it passes the token budget, the turn cap or (if
  set) its own dollar estimate.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from orchestrator.config import env_int

# List prices per million tokens (input, output), USD.
PRICES_PER_MTOK = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
}
CACHE_READ_FACTOR = 0.1     # of the input price
CACHE_WRITE_FACTOR = 1.25   # of the input price (5-minute cache)


def token_budget() -> int:
    return env_int("SESSION_TOKEN_BUDGET", 200_000)


def turn_cap() -> int:
    return env_int("SESSION_TURN_CAP", 40)


def platform_budget(model: str, tokens: int | None = None,
                    max_usd: float | None = None) -> dict:
    """The sessions.create(budget=...) value: max_usd if given, otherwise the
    token budget priced at this model's output rate."""
    if max_usd is not None:
        cents = max(1, math.floor(max_usd * 100))
    else:
        tokens = token_budget() if tokens is None else tokens
        _, output_price = PRICES_PER_MTOK[model]
        cents = max(1, math.ceil(tokens * output_price / 1_000_000 * 100))
    return {"type": "limit", "max_list_cost": {"amount": str(cents), "currency": "USD"}}


@dataclass
class SpendMeter:
    budget_tokens: int = field(default_factory=token_budget)
    max_turns: int = field(default_factory=turn_cap)
    model: str | None = None        # needed for cost_usd
    max_usd: float | None = None    # optional client-side dollar stop
    tokens: int = 0
    turns: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def add(self, model_usage) -> None:
        """Count one span.model_request_end's model_usage."""
        def get(k: str) -> int:
            return getattr(model_usage, k, 0) or 0
        self.turns += 1
        self.input_tokens += get("input_tokens")
        self.output_tokens += get("output_tokens")
        self.cache_read_tokens += get("cache_read_input_tokens")
        self.cache_write_tokens += get("cache_creation_input_tokens")
        self.tokens = (self.input_tokens + self.output_tokens
                       + self.cache_read_tokens + self.cache_write_tokens)

    @property
    def cost_usd(self) -> float:
        """Estimated list cost of the model tokens so far (0 if no model set)."""
        if self.model not in PRICES_PER_MTOK:
            return 0.0
        inp, out = PRICES_PER_MTOK[self.model]
        return (self.input_tokens * inp + self.output_tokens * out
                + self.cache_read_tokens * inp * CACHE_READ_FACTOR
                + self.cache_write_tokens * inp * CACHE_WRITE_FACTOR) / 1_000_000

    @property
    def exceeded(self) -> str | None:
        if self.tokens >= self.budget_tokens:
            return f"token budget reached ({self.tokens:,} of {self.budget_tokens:,})"
        if self.turns >= self.max_turns:
            return f"turn cap reached ({self.turns} of {self.max_turns})"
        if self.max_usd is not None and self.cost_usd >= self.max_usd:
            return f"dollar cap reached (~${self.cost_usd:.2f} of ${self.max_usd:.2f})"
        return None
