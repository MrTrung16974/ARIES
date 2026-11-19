"""Giá ước tính (USD / 1M token) để hiển thị chi phí và đặt quota. Không phải hóa đơn.

Nguồn: bảng giá Claude (cache ngày 2026-11-03). Cache write tính theo mức 5 phút (1,25× input).
"""

from __future__ import annotations

from dataclasses import dataclass

from aiagent.shared_kernel.usage import Usage


@dataclass(frozen=True)
class Price:
    input: float
    output: float
    cache_read: float
    cache_write: float


PRICES: dict[str, Price] = {
    "claude-opus-5-5": Price(input=4.00, output=20.00, cache_read=0.20, cache_write=5.00),
    "claude-sonnet-5-5": Price(input=2.00, output=10.00, cache_read=0.10, cache_write=2.50),
    "claude-haiku-5-5": Price(input=0.10, output=0.50, cache_read=0.01, cache_write=0.125),
    "fake-echo": Price(0.0, 0.0, 0.0, 0.0),
}


def estimate_cost(model: str, usage: Usage) -> float:
    price = PRICES.get(model)
    if price is None:
        return 0.0
    total = (
        usage.input_tokens * price.input
        + usage.output_tokens * price.output
        + usage.cache_read_input_tokens * price.cache_read
        + usage.cache_creation_input_tokens * price.cache_write
    )
    return round(total / 1_000_000, 6)
