from __future__ import annotations

from dataclasses import dataclass
from typing import List, Literal

import numpy as np
import pandas as pd


@dataclass
class Position:
    symbol: str
    direction: Literal["LONG", "SHORT"]
    size: float
    entry: float
    stop_loss: float
    take_profit: float
    risk_amount: float


def calculate_position(
    capital: float,
    atr: float,
    symbol: str,
    direction: Literal["LONG", "SHORT"],
    entry_price: float,
    risk_per_trade: float = 0.02,
    atr_sl_mult: float = 1.5,
    atr_tp_mult: float = 3.0,
) -> Position:
    if capital <= 0 or atr <= 0 or entry_price <= 0:
        raise ValueError("capital, atr, entry_price must be positive")
    risk_amount = capital * risk_per_trade
    sl_dist, tp_dist = atr * atr_sl_mult, atr * atr_tp_mult
    size = risk_amount / sl_dist
    return Position(
        symbol=symbol,
        direction=direction,
        size=float(size),
        entry=float(entry_price),
        stop_loss=float(entry_price - sl_dist if direction == "LONG" else entry_price + sl_dist),
        take_profit=float(entry_price + tp_dist if direction == "LONG" else entry_price - tp_dist),
        risk_amount=float(risk_amount),
    )


def _returns(data) -> pd.Series:
    s = data["close"] if isinstance(data, pd.DataFrame) and "close" in data else pd.Series(data)
    return pd.to_numeric(s, errors="coerce").pct_change().dropna()


def check_correlation_filter(
    new_symbol: str,
    active_positions: List[Position],
    price_history: dict,
    threshold: float = 0.7,
) -> bool:
    if not active_positions or new_symbol not in price_history:
        return True
    new_ret = _returns(price_history[new_symbol])
    if new_ret.empty:
        return True
    symbols = [p.symbol for p in active_positions if p.symbol in price_history]
    if not symbols:
        return True
    frame = pd.concat({"new": new_ret, **{s: _returns(price_history[s]) for s in symbols}}, axis=1).dropna()
    return True if frame.empty else bool(frame.corr().loc["new", symbols].abs().replace([np.inf, -np.inf], np.nan).max() <= threshold)
