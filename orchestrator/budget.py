"""Per-session token budget and turn cap.

SESSION_TOKEN_BUDGET is in tokens, but Managed Agents caps spend in US cents.
Every session gets both:
- a hard platform cap: the token budget priced as if every token were output
  (the dearest kind), so the platform never stops a session before we would;
- a client-side meter that counts real tokens from span.model_request_end and
  interrupts the session when it passes the token budget or the turn cap.
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


def token_budget() -> int:
    return env_int("SESSION_TOKEN_BUDGET", 200_000)


def turn_cap() -> int:
    return env_int("SESSION_TURN_CAP", 40)


def platform_budget(model: str, tokens: int | None = None) -> dict:
    """The sessions.create(budget=...) value for a token budget on this model."""
    tokens = token_budget() if tokens is None else tokens
    _, output_price = PRICES_PER_MTOK[model]
    cents = max(1, math.ceil(tokens * output_price / 1_000_000 * 100))
    return {"type": "limit", "max_list_cost": {"amount": str(cents), "currency": "USD"}}


@dataclass
class SpendMeter:
    budget_tokens: int = field(default_factory=token_budget)
    max_turns: int = field(default_factory=turn_cap)
    tokens: int = 0
    turns: int = 0

    def add(self, model_usage) -> None:
        """Count one span.model_request_end's model_usage."""
        self.turns += 1
        self.tokens += sum(getattr(model_usage, k, 0) or 0 for k in (
            "input_tokens", "output_tokens",
            "cache_read_input_tokens", "cache_creation_input_tokens"))

    @property
    def exceeded(self) -> str | None:
        if self.tokens >= self.budget_tokens:
            return f"token budget reached ({self.tokens:,} of {self.budget_tokens:,})"
        if self.turns >= self.max_turns:
            return f"turn cap reached ({self.turns} of {self.max_turns})"
        return None
