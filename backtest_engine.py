from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict

import numpy as np
import pandas as pd


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    return 100 - (100 / (1 + gain / loss.replace(0, np.nan)))


def _max_dd(equity: pd.Series) -> float:
    return float((equity / equity.cummax() - 1).min() * 100)


def calculate_metrics(returns: pd.Series) -> Dict[str, float]:
    r = pd.to_numeric(returns, errors="coerce").dropna()
    if r.empty:
        return {k: 0.0 for k in ("sharpe", "sortino", "max_drawdown_pct", "calmar", "win_rate_pct", "profit_factor")}
    equity = (1 + r).cumprod()
    periods = 365 * 24
    downside = r[r < 0]
    std, down_std = r.std(ddof=0), downside.std(ddof=0)
    sharpe = 0.0 if std == 0 or pd.isna(std) else (r.mean() / std) * np.sqrt(periods)
    sortino = 0.0 if down_std == 0 or pd.isna(down_std) else (r.mean() / down_std) * np.sqrt(periods)
    max_dd = _max_dd(equity)
    annual = equity.iloc[-1] ** (periods / max(len(r), 1)) - 1
    losses = r[r < 0].abs().sum()
    return {
        "sharpe": float(sharpe),
        "sortino": float(sortino),
        "max_drawdown_pct": max_dd,
        "calmar": float(0 if max_dd == 0 else annual / abs(max_dd / 100)),
        "win_rate_pct": float((r > 0).mean() * 100),
        "profit_factor": float(np.inf if losses == 0 and r[r > 0].sum() > 0 else 0 if losses == 0 else r[r > 0].sum() / losses),
    }


def _strategy_returns(df: pd.DataFrame, funding: pd.DataFrame, initial_capital: float) -> pd.Series:
    close = pd.to_numeric(df["close"], errors="coerce")
    ema50, ema200 = close.ewm(span=50, adjust=False).mean(), close.ewm(span=200, adjust=False).mean()
    trend = pd.Series(np.select([close.gt(ema50) & ema50.gt(ema200), close.lt(ema50) & ema50.lt(ema200)], [1, -1], 0), index=df.index)
    rsi = _rsi(close)
    momentum = ((rsi - rsi.rolling(20).mean()) / rsi.rolling(20).std(ddof=0)).clip(-3, 3) / 3
    f = pd.to_numeric(funding.reindex(df.index).ffill().bfill().iloc[:, 0], errors="coerce") if not funding.empty else pd.Series(0, index=df.index)
    funding_score = (((f - f.rolling(720).min()) / (f.rolling(720).max() - f.rolling(720).min())) * 2 - 1).fillna(0).clip(-1, 1)
    zero = pd.Series(0.0, index=df.index)
    score = trend * 0.25 + momentum.fillna(0) * 0.20 + funding_score * 0.15 + zero * 0.40
    pos = pd.Series(np.select([score.gt(0.65) & trend.eq(1), score.lt(-0.65) & trend.eq(-1)], [1, -1], 0), index=df.index)
    try:
        import backtesting as bt  # noqa: F401
    except Exception:
        pass
    return (pos.shift().fillna(0) * close.pct_change().fillna(0)).rename("returns")


def run_walk_forward(
    ohlcv_df: pd.DataFrame,
    funding_df: pd.DataFrame,
    signal_fn: Callable,
    train_days: int = 60,
    test_days: int = 15,
    step_days: int = 15,
    initial_capital: float = 1000.0,
) -> pd.DataFrame:
    df = ohlcv_df.copy()
    df.index = pd.to_datetime(df.index, utc=True)
    funding = funding_df.copy()
    if not funding.empty:
        funding.index = pd.to_datetime(funding.index, utc=True)
    start, end, rows, all_returns = df.index.min(), df.index.max(), [], []
    cursor = start
    while cursor + pd.Timedelta(days=train_days + test_days) <= end:
        train_end, test_end = cursor + pd.Timedelta(days=train_days), cursor + pd.Timedelta(days=train_days + test_days)
        test = df.loc[train_end:test_end]
        rets = _strategy_returns(test, funding.loc[train_end:test_end] if not funding.empty else funding, initial_capital)
        metrics = calculate_metrics(rets)
        rows.append({"train_start": cursor.isoformat(), "train_end": train_end.isoformat(), "test_end": test_end.isoformat(), **metrics})
        all_returns.append(rets)
        cursor += pd.Timedelta(days=step_days)
    result = pd.DataFrame(rows)
    payload = {"generated_at": datetime.now(timezone.utc).isoformat(), "windows": rows, "aggregate": calculate_metrics(pd.concat(all_returns) if all_returns else pd.Series(dtype=float))}
    out = Path("backtest_results") / f"{datetime.now(timezone.utc).date()}_strategy.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, default=float), encoding="utf-8")
    return result
