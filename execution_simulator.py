from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import random
import time

from signal_model import Signal


@dataclass
class FillResult:
    symbol: str
    direction: str
    filled_qty: float
    fill_price: float
    slippage_pct: float
    fee_paid: float
    latency_ms: float
    timestamp: str


def simulate_fill(
    signal: Signal,
    last_price: float,
    volume_24h: float,
    volume_15m: float,
    fee_rate: float = 0.0006,
) -> FillResult:
    latency_ms = random.uniform(150, 500)
    time.sleep(latency_ms / 1000)
    vol_pct = 1 if volume_24h <= 0 else max(0, min(1, volume_15m / (volume_24h / 96)))
    slippage_pct = random.uniform(0.0002, 0.0008) * (1 - vol_pct) + 0.0002 * vol_pct
    side_mult = 1 if signal.direction == "LONG" else -1
    qty = float(signal.position_size)
    parts = 2 if volume_15m > 0 and qty > volume_15m * 0.10 else 1
    fill_price = last_price * (1 + side_mult * slippage_pct)
    filled_qty = qty
    fee_paid = filled_qty * fill_price * fee_rate * parts
    return FillResult(
        symbol=signal.symbol,
        direction=signal.direction,
        filled_qty=filled_qty,
        fill_price=float(fill_price),
        slippage_pct=float(slippage_pct),
        fee_paid=float(fee_paid),
        latency_ms=float(latency_ms),
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
