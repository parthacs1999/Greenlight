"""Approximate list prices (USD per 1M tokens) used for cost estimates and the cost cap.

Estimates only: cache writes and long-context tiers aren't modelled. For models not
listed here, pass --input-price/--output-price, or the run relies on the step and
token budgets alone.
"""

from __future__ import annotations

from dataclasses import dataclass

from .providers.base import Usage

CACHE_READ_MULTIPLIER = 0.1


@dataclass(frozen=True)
class Price:
    input_per_mtok: float
    output_per_mtok: float

    def cost(self, usage: Usage) -> float:
        uncached = usage.input_tokens - usage.cached_input_tokens
        input_cost = (uncached + usage.cached_input_tokens * CACHE_READ_MULTIPLIER) * (
            self.input_per_mtok / 1_000_000
        )
        return input_cost + usage.output_tokens * self.output_per_mtok / 1_000_000


PRICES: dict[str, Price] = {
    "claude-fable-5-1": Price(10.0, 50.0),
    "claude-opus-5-5": Price(4.0, 20.0),
    "claude-opus-5": Price(5.0, 25.0),
    "claude-sonnet-5-5": Price(2.0, 10.0),
    "claude-haiku-5-5": Price(0.10, 0.50),
}


def price_for(model: str) -> Price | None:
    return PRICES.get(model)
