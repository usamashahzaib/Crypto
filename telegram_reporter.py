from __future__ import annotations

import requests

from execution_simulator import FillResult
from signal_model import Signal


class TelegramReporter:
    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    def _send(self, text: str) -> bool:
        if not self.bot_token or not self.chat_id:
            return False
        try:
            return bool(requests.post(self.url, json={"chat_id": self.chat_id, "text": text}, timeout=10).ok)
        except requests.RequestException:
            return False

    def send_signal_alert(self, signal: Signal, fill: FillResult) -> bool:
        risk = abs(signal.entry_price - signal.stop_loss)
        reward = abs(signal.take_profit - signal.entry_price)
        rr = 0 if risk == 0 else reward / risk
        return self._send(
            "\n".join([
                "SIGNAL",
                f"{signal.symbol} {signal.direction}",
                f"Score: {signal.score:.3f}",
                f"Entry: {fill.fill_price:.4f}",
                f"SL: {signal.stop_loss:.4f}",
                f"TP: {signal.take_profit:.4f}",
                f"Size: {fill.filled_qty:.6f}",
                f"R/R: {rr:.2f}",
            ])
        )

    def send_daily_summary(self, pnl: float, positions: int, win_rate: float, max_dd: float) -> bool:
        return self._send(f"DAILY\nP&L: {pnl:.2f}\nOpen: {positions}\nWin Rate: {win_rate:.2f}%\nWorst DD: {max_dd:.2f}%")

    def send_weekly_report(self, metrics: dict) -> bool:
        return self._send(
            "\n".join([
                "WEEKLY",
                f"Sharpe: {metrics.get('sharpe', 0):.2f}",
                f"Max DD: {metrics.get('max_drawdown_pct', 0):.2f}%",
                f"Best: {metrics.get('best_trade', 0):.2f}",
                f"Worst: {metrics.get('worst_trade', 0):.2f}",
                f"Trades: {metrics.get('total_trades', 0)}",
            ])
        )
