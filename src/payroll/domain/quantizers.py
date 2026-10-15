"""Backward-compatible exports for shared income quantizers."""

from income.domain.quantizers import (
    quantize_clp,
    quantize_currency_amount,
    quantize_percent,
    quantize_utm,
)

__all__ = [
    "quantize_clp",
    "quantize_currency_amount",
    "quantize_percent",
    "quantize_utm",
]
