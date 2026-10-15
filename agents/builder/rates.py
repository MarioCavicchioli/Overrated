"""2026 business rates multipliers (England) and the annual saving.

The multiplier applies to the whole rateable value, and the band is set by that
value, so dropping below £51,000 lowers the rate on everything, not just the drop.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

SMALL_BUSINESS_LIMIT = 51_000   # below this: small business multiplier
HIGH_VALUE_FROM = 500_000       # this and above: high-value multiplier

# Retail, hospitality and leisure uses get the lower RHL multipliers. The mock uses
# words; real data uses the VOA SCat code ("249" = shops, as in Person 1's data
# service; add hospitality and leisure codes when we go beyond shops).
RHL_USES = {"shop", "cafe", "restaurant", "249"}


def use_of(valuation: dict) -> str:
    """What decides the multiplier: the VOA code when the data service gives one."""
    return valuation.get("use_class") or valuation["use"]

MULTIPLIERS = {
    # band:        (RHL,               other)
    "small":      (Decimal("0.382"), Decimal("0.432")),
    "standard":   (Decimal("0.430"), Decimal("0.480")),
    "high_value": (Decimal("0.508"), Decimal("0.508")),
}


def band(rv: int) -> str:
    if rv < SMALL_BUSINESS_LIMIT:
        return "small"
    if rv < HIGH_VALUE_FROM:
        return "standard"
    return "high_value"


def multiplier(rv: int, use: str) -> Decimal:
    rhl, other = MULTIPLIERS[band(rv)]
    return rhl if use.lower() in RHL_USES else other


def bill(rv: int, use: str) -> Decimal:
    return Decimal(rv) * multiplier(rv, use)


def annual_saving(current_rv: int, proposed_rv: int, use: str) -> int:
    """bill(current) - bill(proposed), in whole pounds."""
    saving = bill(current_rv, use) - bill(proposed_rv, use)
    return int(saving.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
