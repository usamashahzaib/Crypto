from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Optional

import numpy as np
import pandas as pd


@dataclass
class Signal:
    symbol: str
    direction: Literal["LONG", "SHORT"]
    score: float
    entry_price: float
    stop_loss: float
    take_profit: float
    position_size: float
    timestamp: str


WEIGHTS = {
    "trend": 0.25,
    "momentum": 0.20,
    "funding": 0.15,
    "oi_delta": 0.15,
    "whale": 0.15,
    "sentiment": 0.10,
}


def _series(df: pd.DataFrame, name: str) -> pd.Series:
    if name in df:
        return pd.to_numeric(df[name], errors="coerce")
    idx = {"open": 0, "high": 1, "low": 2, "close": 3, "volume": 4}.get(name)
    return pd.to_numeric(df.iloc[:, idx], errors="coerce")


def _symbol(df: pd.DataFrame) -> str:
    return str(df["symbol"].iloc[-1]) if "symbol" in df and len(df) else "UNKNOWN"


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    return 100 - (100 / (1 + gain / loss.replace(0, np.nan)))


def _atr(df: pd.DataFrame, period: int = 14) -> float:
    high, low, close = (_series(df, c) for c in ("high", "low", "close"))
    tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    val = tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean().iloc[-1]
    return float(val) if np.isfinite(val) and val > 0 else float((high - low).tail(period).mean())


def _cooldown_ok(last_signal_time: Optional[str], cooldown_hours: int) -> bool:
    if not last_signal_time:
        return True
    last = pd.Timestamp(last_signal_time)
    last = last.tz_localize("UTC") if last.tzinfo is None else last.tz_convert("UTC")
    return pd.Timestamp.now(tz="UTC") - last >= pd.Timedelta(hours=cooldown_hours)


def _norm_whale(flow: float) -> float:
    return float(np.tanh(flow))


def generate_signal(
    ohlcv_4h: pd.DataFrame,
    ohlcv_15m: pd.DataFrame,
    funding_rate: float,
    open_interest: float,
    whale_flow: float,
    fear_greed: int,
    last_signal_time: Optional[str] = None,
    cooldown_hours: int = 4,
) -> Optional[Signal]:
    if len(ohlcv_4h) < 200 or len(ohlcv_15m) < 35 or not _cooldown_ok(last_signal_time, cooldown_hours):
        return None

    close_4h = _series(ohlcv_4h, "close")
    close_15m = _series(ohlcv_15m, "close")
    ema50, ema200 = close_4h.ewm(span=50, adjust=False).mean(), close_4h.ewm(span=200, adjust=False).mean()
    price = float(close_4h.iloc[-1])
    trend = 1 if price > ema50.iloc[-1] > ema200.iloc[-1] else -1 if price < ema50.iloc[-1] < ema200.iloc[-1] else 0
    if trend == 0:
        return None

    rsi = _rsi(close_15m)
    rsi_window = rsi.tail(20)
    rsi_std = float(rsi_window.std(ddof=0))
    momentum = 0.0 if rsi_std == 0 or not np.isfinite(rsi_std) else float(np.clip((rsi.iloc[-1] - rsi_window.mean()) / rsi_std, -3, 3) / 3)

    funding_window = pd.to_numeric(ohlcv_4h.get("funding_rate", pd.Series([funding_rate] * len(ohlcv_4h))), errors="coerce").tail(180)
    f_min, f_max = float(funding_window.min()), float(funding_window.max())
    funding = 0.0 if f_max == f_min else ((funding_rate - f_min) / (f_max - f_min) * 2) - 1

    oi_series = pd.to_numeric(ohlcv_4h.get("open_interest", pd.Series([open_interest] * len(ohlcv_4h))), errors="coerce")
    baseline_oi = float(oi_series.tail(2).iloc[0]) if len(oi_series.dropna()) >= 2 else float(open_interest)
    oi_delta = 0.0 if baseline_oi == 0 else float(np.clip((open_interest - baseline_oi) / baseline_oi, -1, 1))

    fg_series = pd.to_numeric(ohlcv_4h.get("fear_greed", pd.Series([fear_greed] * len(ohlcv_4h))), errors="coerce").tail(42)
    sentiment = float(np.clip((fear_greed - fg_series.mean()) / 25, -1, 1))

    components = {
        "trend": trend,
        "momentum": np.clip(momentum, -1, 1),
        "funding": np.clip(funding, -1, 1),
        "oi_delta": oi_delta,
        "whale": _norm_whale(whale_flow),
        "sentiment": sentiment,
    }
    score = float(sum(components[k] * WEIGHTS[k] for k in WEIGHTS))
    direction: Literal["LONG", "SHORT"] | None = "LONG" if score > 0.65 and trend == 1 else "SHORT" if score < -0.65 and trend == -1 else None
    if direction is None:
        return None

    atr = _atr(ohlcv_4h)
    sl_dist, tp_dist = atr * 1.5, atr * 3.0
    return Signal(
        symbol=_symbol(ohlcv_4h),
        direction=direction,
        score=score,
        entry_price=price,
        stop_loss=price - sl_dist if direction == "LONG" else price + sl_dist,
        take_profit=price + tp_dist if direction == "LONG" else price - tp_dist,
        position_size=0.0,
        timestamp=_iso_now(),
    )
