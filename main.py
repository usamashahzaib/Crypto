from __future__ import annotations

import argparse
import json
import logging
import os
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import yaml

from backtest_engine import calculate_metrics, run_walk_forward
from data_fetcher import DataFetcher
from execution_simulator import simulate_fill
from risk_engine import Position, calculate_position, check_correlation_filter
from signal_model import generate_signal
from telegram_reporter import TelegramReporter


PKT = ZoneInfo("Asia/Karachi")
STATE_FILE = Path("bot_state.json")


def load_config(path: str = "config.yaml") -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def setup_logging(cfg: dict) -> None:
    path = Path(cfg["logging"]["path"])
    path.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=getattr(logging, cfg["logging"]["level"]),
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(path / "bot.log", encoding="utf-8"), logging.StreamHandler()],
    )


def _state() -> dict:
    return json.loads(STATE_FILE.read_text(encoding="utf-8")) if STATE_FILE.exists() else {"positions": [], "last_signal_time": {}, "trades": []}


def _save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _atr(df: pd.DataFrame, period: int = 14) -> float:
    tr = pd.concat([df["high"] - df["low"], (df["high"] - df["close"].shift()).abs(), (df["low"] - df["close"].shift()).abs()], axis=1).max(axis=1)
    return float(tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean().iloc[-1])


def _log_decision(cfg: dict, payload: dict) -> None:
    path = Path(cfg["logging"]["path"]) / "signals_log.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, default=str) + "\n")


def _symbol(exchange_name: str, base: str) -> str:
    return f"{base}/USDT:USDT" if "futures" in exchange_name else f"{base}/USDT"


def backtest_mode(cfg: dict) -> None:
    fetcher = DataFetcher("binance")
    start = pd.Timestamp(cfg["backtest"]["start_date"], tz="UTC")
    df = fetcher.fetch_ohlcv_since(_symbol(cfg["exchange"]["name"], "BTC"), "1h", cfg["backtest"]["start_date"])
    funding = pd.DataFrame({"funding": [fetcher.fetch_funding_rate(_symbol(cfg["exchange"]["name"], "BTC"))]}, index=[df.index[-1]]) if not df.empty else pd.DataFrame()
    result = run_walk_forward(
        df,
        funding,
        generate_signal,
        train_days=cfg["backtest"]["train_days"],
        test_days=cfg["backtest"]["test_days"],
        step_days=cfg["backtest"]["step_days"],
        initial_capital=cfg["capital"]["initial"],
    )
    print(result.to_string(index=False) if not result.empty else "No backtest windows generated")


def _paper_once(cfg: dict, fetcher: DataFetcher, reporter: TelegramReporter, state: dict) -> None:
    symbols = [_symbol(cfg["exchange"]["name"], s) for s in ("BTC", "ETH", "SOL", "BNB")]
    histories, now = {}, datetime.now(PKT)
    active = [Position(**p) for p in state["positions"]]
    for symbol in symbols:
        try:
            o4h, o15 = fetcher.fetch_ohlcv(symbol, "4h"), fetcher.fetch_ohlcv(symbol, "15m")
            histories[symbol] = o4h
            whale = fetcher.fetch_whale_eth() if symbol.startswith("ETH") else fetcher.fetch_whale_btc() if symbol.startswith("BTC") else 0.0
            signal = generate_signal(
                o4h,
                o15,
                fetcher.fetch_funding_rate(symbol),
                fetcher.fetch_open_interest(symbol),
                whale,
                fetcher.fetch_fear_greed(),
                state["last_signal_time"].get(symbol),
                cfg["signals"]["cooldown_hours"],
            )
            payload = {"ts": datetime.now(PKT).isoformat(), "symbol": symbol, "signal": asdict(signal) if signal else None}
            if not signal or len(active) >= cfg["risk"]["max_positions"]:
                _log_decision(cfg, {**payload, "accepted": False, "reason": "no_signal_or_max_positions"})
                continue
            atr = _atr(o4h)
            pos = calculate_position(
                cfg["capital"]["initial"],
                atr,
                symbol,
                signal.direction,
                signal.entry_price,
                cfg["capital"]["risk_per_trade"],
                cfg["risk"]["atr_multiplier_sl"],
                cfg["risk"]["atr_multiplier_tp"],
            )
            if not check_correlation_filter(symbol, active, histories, cfg["risk"]["correlation_threshold"]):
                _log_decision(cfg, {**payload, "accepted": False, "reason": "correlation"})
                continue
            signal.position_size, signal.stop_loss, signal.take_profit = pos.size, pos.stop_loss, pos.take_profit
            fill = simulate_fill(signal, float(o15["close"].iloc[-1]), float(o15["volume"].tail(96).sum()), float(o15["volume"].iloc[-1]), cfg["exchange"]["fee_taker"])
            active.append(pos)
            state["positions"], state["last_signal_time"][symbol] = [asdict(p) for p in active], signal.timestamp
            state["trades"].append({"signal": asdict(signal), "fill": asdict(fill)})
            reporter.send_signal_alert(signal, fill)
            _log_decision(cfg, {**payload, "accepted": True, "position": asdict(pos), "fill": asdict(fill)})
        except Exception as exc:
            logging.exception("paper decision failed: %s", symbol)
            _log_decision(cfg, {"ts": datetime.now(PKT).isoformat(), "symbol": symbol, "accepted": False, "error": str(exc)})
    if now.hour == 8 and now.minute < 15:
        reporter.send_daily_summary(0.0, len(active), 0.0, 0.0)
    if now.weekday() == 0 and now.hour == 9 and now.minute < 15:
        reporter.send_weekly_report({"sharpe": 0, "max_drawdown_pct": 0, "best_trade": 0, "worst_trade": 0, "total_trades": len(state["trades"])})
    _save_state(state)


def paper_mode(cfg: dict) -> None:
    fetcher = DataFetcher("binance")
    reporter = TelegramReporter(os.getenv("TELEGRAM_TOKEN", os.getenv("TELEGRAM_BOT_TOKEN", "")), os.getenv("TELEGRAM_CHAT_ID", ""))
    state = _state()
    while True:
        _paper_once(cfg, fetcher, reporter, state)
        time.sleep(900)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["backtest", "paper"])
    args, cfg = parser.parse_args(), load_config()
    setup_logging(cfg)
    backtest_mode(cfg) if args.mode == "backtest" else paper_mode(cfg)


if __name__ == "__main__":
    main()
