# CryptoMind automated crypto analysis bot.
# Beginner rule: edit only the .env file. Keep this file unchanged unless needed.

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import feedparser
import pandas as pd
import requests
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
from groq import Groq
from pytrends.request import TrendReq
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator, MACD

try:
    from binance.client import Client as BinanceClient
except Exception:
    BinanceClient = None


BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"
LOG_FILE = BASE_DIR / "signals_log.txt"
STATE_FILE = BASE_DIR / "bot_state.json"
HISTORY_FILE = BASE_DIR / "signals_history.json"
PKT = ZoneInfo("Asia/Karachi")
PAPER_MODE = True
TOTAL_CAPITAL = 30.0  # In USD.
MAX_ACTIVE_POSITIONS = 3
MIN_TRADE_USD = 10.0
MAX_TRADE_USD = 15.0
ACTIVE_TRADES = []
SCHEDULER = None

load_dotenv(ENV_FILE)  # Local only. GitHub Actions uses repository secrets via os.environ.


def load_combined_secret_blob(blob: str) -> None:
    """Support one GitHub Secret containing many KEY=VALUE lines or JSON."""
    if not blob:
        return
    try:
        data = json.loads(blob)
        if isinstance(data, dict):
            for key, value in data.items():
                os.environ.setdefault(str(key).upper(), str(value).strip())
            return
    except Exception:
        pass
    for raw in blob.replace(";", "\n").splitlines():
        if "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        os.environ.setdefault(key.strip().upper(), value.strip().strip('"').strip("'"))


def load_telegram_blob(blob: str) -> None:
    """Support one Telegram secret containing token + chat ID."""
    if not blob:
        return
    load_combined_secret_blob(blob)
    token = os.environ.get("TELEGRAM_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token:
        match = re.search(r"\d{6,}:[A-Za-z0-9_-]{20,}", blob)
        if match:
            os.environ["TELEGRAM_TOKEN"] = match.group(0)
    if not chat_id:
        ids = re.findall(r"(?<!:)-?\d{5,}", blob)
        bot_id = os.environ.get("TELEGRAM_TOKEN", "").split(":", 1)[0]
        for value in ids:
            if value != bot_id:
                os.environ["TELEGRAM_CHAT_ID"] = value
                break


load_combined_secret_blob(os.environ.get("CRYPTO_SECRET", ""))
load_telegram_blob(os.environ.get("CRYPTO_TELEGRAM_BOT", ""))

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
BINANCE_API_KEY = os.environ.get("BINANCE_API_KEY", "")
BINANCE_API_SECRET = os.environ.get("BINANCE_API_SECRET", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
NEWSAPI_KEY = os.environ.get("NEWSAPI_KEY", "")
COINGECKO_KEY = os.environ.get("COINGECKO_KEY", "")
ETHERSCAN_KEY = os.environ.get("ETHERSCAN_KEY", "")

COINS = {
    "BTC": {"cg": "bitcoin", "binance": "BTCUSDT"},
    "ETH": {"cg": "ethereum", "binance": "ETHUSDT"},
    "SOL": {"cg": "solana", "binance": "SOLUSDT"},
}

BULL_WORDS = {
    "buy", "accumulate", "bullish", "breakout", "surge", "rally", "pump",
    "approval", "adoption", "etf", "institutional", "loading", "long",
}
BEAR_WORDS = {
    "sell", "bearish", "crash", "dump", "hack", "lawsuit", "ban", "short",
    "liquidation", "fear", "recession", "probe", "exploit", "outflow",
}
INFLUENCER_WORDS = {"buy", "accumulate", "bullish", "bitcoin", "loading"}
NITTER_FEEDS = {
    "Michael Saylor": "https://nitter.poast.org/michael_saylor/rss",
    "Anthony Pompliano": "https://nitter.poast.org/APompliano/rss",
    "Arthur Hayes": "https://nitter.poast.org/CryptoHayes/rss",
    "CZ Binance": "https://nitter.poast.org/cz_binance/rss",
}
NEWS_RSS_FEEDS = {
    "Cointelegraph": "https://cointelegraph.com/rss",
    "CoinDesk": "https://coindesk.com/arc/outboundfeeds/rss/",
    "Decrypt": "https://decrypt.co/feed",
    "Bitcoin Magazine": "https://bitcoinmagazine.com/.rss/full/",
}


@dataclass
class Config:
    coingecko_key: str = ""
    newsapi_key: str = ""
    groq_api_key: str = ""
    binance_api_key: str = ""
    binance_api_secret: str = ""
    gemini_api_key: str = ""
    telegram_token: str = ""
    telegram_chat_id: str = ""
    etherscan_key: str = ""


def load_env() -> Config:
    """Map loaded environment variables into one beginner-friendly config object."""
    return Config(
        coingecko_key=COINGECKO_KEY,
        newsapi_key=NEWSAPI_KEY,
        groq_api_key=GROQ_API_KEY,
        binance_api_key=BINANCE_API_KEY,
        binance_api_secret=BINANCE_API_SECRET,
        gemini_api_key=GEMINI_API_KEY,
        telegram_token=TELEGRAM_TOKEN,
        telegram_chat_id=TELEGRAM_CHAT_ID,
        etherscan_key=ETHERSCAN_KEY,
    )


CFG = load_env()


def usable(value: str) -> bool:
    """Reject empty placeholder keys."""
    return bool(value and not value.lower().startswith("your_"))


def now_pkt() -> datetime:
    return datetime.now(PKT)


def safe_get_json(url: str, **kwargs: Any) -> Any | None:
    """Return JSON or None. API errors never crash the bot."""
    try:
        res = requests.get(url, timeout=20, **kwargs)
        if res.status_code >= 400:
            return None
        return res.json()
    except Exception:
        return None


def load_state() -> dict[str, Any]:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"daily_count": 0, "daily_date": now_pkt().date().isoformat(), "active_positions": {}}


def save_state(state: dict[str, Any]) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def load_history() -> list[dict[str, Any]]:
    try:
        return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def save_history(rows: list[dict[str, Any]]) -> None:
    HISTORY_FILE.write_text(json.dumps(rows, indent=2), encoding="utf-8")


def next_history_id(rows: list[dict[str, Any]]) -> int:
    return max((int(r.get("id", 0)) for r in rows), default=0) + 1


def reset_daily_if_needed(state: dict[str, Any]) -> dict[str, Any]:
    today = now_pkt().date().isoformat()
    state.setdefault("active_positions", {})
    sync_active_trades(state)
    if state.get("daily_date") != today:
        state["daily_date"], state["daily_count"] = today, 0
    return state


def sync_active_trades(state: dict[str, Any]) -> None:
    """Keep beginner-friendly ACTIVE_TRADES synced with saved bot state."""
    ACTIVE_TRADES.clear()
    for coin, trade in (state.get("active_positions") or {}).items():
        if not isinstance(trade, dict):
            continue
        ACTIVE_TRADES.append({"coin": coin, **trade})


def active_position_count(state: dict[str, Any]) -> int:
    sync_active_trades(state)
    return len(ACTIVE_TRADES)


def max_available_allocation(state: dict[str, Any]) -> float:
    active = active_position_count(state)
    if active >= MAX_ACTIVE_POSITIONS:
        return 0.0
    remaining_capital = max(0.0, TOTAL_CAPITAL - (active * MIN_TRADE_USD))
    return min(MAX_TRADE_USD, remaining_capital)


def default_allocation(state: dict[str, Any]) -> float:
    available = max_available_allocation(state)
    return MIN_TRADE_USD if available >= MIN_TRADE_USD else 0.0


def get_coingecko_prices() -> dict[str, Any]:
    ids = ",".join(c["cg"] for c in COINS.values())
    url = "https://api.coingecko.com/api/v3/simple/price"
    headers = {"x-cg-demo-api-key": CFG.coingecko_key} if usable(CFG.coingecko_key) else {}
    params = {"ids": ids, "vs_currencies": "usd", "include_24hr_change": "true"}
    data = safe_get_json(url, headers=headers, params=params) or {}

    prices = {}
    for symbol, meta in COINS.items():
        coin = data.get(meta["cg"], {})
        prices[symbol] = {
            "price": coin.get("usd"),
            "change_24h": coin.get("usd_24h_change"),
        }
    return prices


def get_binance_indicators() -> dict[str, Any]:
    """Pull 15m candles and calculate RSI + MACD."""
    out = {}
    client = None
    try:
        if BinanceClient:
            client = BinanceClient(
                CFG.binance_api_key if usable(CFG.binance_api_key) else None,
                CFG.binance_api_secret if usable(CFG.binance_api_secret) else None,
            )
    except Exception:
        client = None

    for symbol, meta in COINS.items():
        try:
            if client:
                klines = client.get_klines(symbol=meta["binance"], interval="15m", limit=200)
            else:
                url = "https://api.binance.com/api/v3/klines"
                klines = safe_get_json(url, params={"symbol": meta["binance"], "interval": "15m", "limit": 200}) or []

            closes = pd.Series([float(k[4]) for k in klines])
            if len(closes) < 35:
                continue
            macd = MACD(close=closes)
            out[symbol] = {
                "rsi": round(float(RSIIndicator(close=closes, window=14).rsi().iloc[-1]), 2),
                "macd": round(float(macd.macd().iloc[-1]), 4),
                "macd_signal": round(float(macd.macd_signal().iloc[-1]), 4),
                "macd_hist": round(float(macd.macd_diff().iloc[-1]), 4),
            }
        except Exception:
            continue
    return out


def get_macro_trend() -> dict[str, Any]:
    """Use Binance 4H candles and 200 EMA for macro trend."""
    out = {}
    for symbol, meta in COINS.items():
        try:
            url = "https://api.binance.com/api/v3/klines"
            klines = safe_get_json(url, params={"symbol": meta["binance"], "interval": "4h", "limit": 250}) or []
            closes = pd.Series([float(k[4]) for k in klines])
            if len(closes) < 210:
                continue
            ema_200 = float(EMAIndicator(close=closes, window=200).ema_indicator().iloc[-1])
            price = float(closes.iloc[-1])
            out[symbol] = {
                "price": round(price, 4),
                "ema_200": round(ema_200, 4),
                "trend": "Bullish" if price > ema_200 else "Bearish",
            }
        except Exception:
            continue
    return out


def get_futures_data() -> dict[str, Any]:
    """Fetch public Binance Futures funding rate and open interest."""
    out = {}
    for symbol, meta in COINS.items():
        try:
            pair = meta["binance"]
            funding = safe_get_json("https://fapi.binance.com/fapi/v1/premiumIndex", params={"symbol": pair}) or {}
            oi = safe_get_json("https://fapi.binance.com/fapi/v1/openInterest", params={"symbol": pair}) or {}
            rate = float(funding.get("lastFundingRate") or 0)
            out[symbol] = {
                "funding_rate": rate,
                "funding_label": funding_label(rate),
                "open_interest": float(oi.get("openInterest") or 0),
            }
        except Exception:
            continue
    return out


def funding_label(rate: float) -> str:
    if rate >= 0.0005:
        return "High"
    if rate <= -0.0002:
        return "Low"
    return "Neutral"


def get_fear_greed() -> dict[str, Any]:
    data = safe_get_json("https://api.alternative.me/fng/", params={"limit": 1}) or {}
    item = (data.get("data") or [{}])[0]
    return {"value": item.get("value"), "label": item.get("value_classification")}


def score_text(text: str) -> int:
    words = set(re.findall(r"[a-zA-Z]+", text.lower()))
    return sum(w in words for w in BULL_WORDS) - sum(w in words for w in BEAR_WORDS)


def get_crypto_news() -> dict[str, Any]:
    items = []
    items.extend(get_newsapi_items())
    items.extend(get_rss_news_items())
    items = sorted(items, key=lambda x: x.get("published_at") or "", reverse=True)[:20]

    positive = sum(i["score"] > 0 for i in items)
    negative = sum(i["score"] < 0 for i in items)
    mood = "BULLISH" if positive > negative else "BEARISH" if negative > positive else "NEUTRAL"
    return {"items": items, "positive": positive, "negative": negative, "mood": mood}


def get_newsapi_items() -> list[dict[str, Any]]:
    """NewsAPI source. Needs NEWSAPI_KEY in .env."""
    if not usable(CFG.newsapi_key):
        return []
    url = "https://newsapi.org/v2/everything"
    params = {
        "q": "bitcoin OR ethereum OR crypto",
        "language": "en",
        "sortBy": "publishedAt",
        "pageSize": 20,
        "apiKey": CFG.newsapi_key,
    }
    data = safe_get_json(url, params=params) or {}
    return [
        news_item(
            title=a.get("title", ""),
            summary=a.get("description", ""),
            url=a.get("url"),
            source=(a.get("source") or {}).get("name", "NewsAPI"),
            published_at=a.get("publishedAt", ""),
        )
        for a in data.get("articles", [])
        if a.get("title")
    ]


def get_rss_news_items() -> list[dict[str, Any]]:
    """Free RSS sources. No API keys."""
    items = []
    for source, url in NEWS_RSS_FEEDS.items():
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:20]:
                items.append(news_item(
                    title=entry.get("title", ""),
                    summary=entry.get("summary", ""),
                    url=entry.get("link"),
                    source=source,
                    published_at=get_rss_date(entry),
                ))
        except Exception:
            continue
    return [i for i in items if i["title"]]


def news_item(title: str, summary: str, url: str | None, source: str, published_at: str) -> dict[str, Any]:
    text = f"{title} {summary}"
    return {"title": title, "score": score_text(text), "url": url, "source": source, "published_at": published_at}


def get_rss_date(entry: Any) -> str:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    return datetime(*parsed[:6], tzinfo=PKT).isoformat() if parsed else ""


def get_google_trends() -> dict[str, Any]:
    try:
        pytrends = TrendReq(hl="en-US", tz=300)
        pytrends.build_payload(["bitcoin"], timeframe="now 7-d")
        df = pytrends.interest_over_time()
        if df.empty or "bitcoin" not in df:
            return {"value": None, "status": "UNKNOWN"}
        values = df["bitcoin"].tail(24)
        last, avg = int(values.iloc[-1]), float(values.iloc[:-1].mean() or 1)
        return {"value": last, "status": "SPIKING" if last >= avg * 1.25 else "NORMAL"}
    except Exception:
        return {"value": None, "status": "UNKNOWN"}


def get_influencer_alerts() -> list[str]:
    alerts = []
    for name, url in NITTER_FEEDS.items():
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:3]:
                text = f"{entry.get('title', '')} {entry.get('summary', '')}".lower()
                if any(word in text for word in INFLUENCER_WORDS):
                    alerts.append(f"{name} posted about {first_matching_word(text, INFLUENCER_WORDS)}")
                    break
        except Exception:
            continue
    return alerts


def first_matching_word(text: str, words: set[str]) -> str:
    return next((w for w in words if w in text), "crypto")


def get_btc_whales() -> dict[str, Any]:
    """Real BTC whale check. Free source; Etherscan cannot read Bitcoin."""
    data = safe_get_json("https://mempool.space/api/mempool/recent") or []
    whales = [
        {"txid": tx.get("txid"), "btc": round((tx.get("value") or 0) / 100_000_000, 2)}
        for tx in data
        if (tx.get("value") or 0) >= 100 * 100_000_000
    ]
    return {"detected": bool(whales), "transactions": whales[:5]}


def get_etherscan_whales() -> dict[str, Any]:
    """Ethereum whale check via Etherscan. Threshold: 100 ETH."""
    if not usable(CFG.etherscan_key):
        return {"detected": False, "transactions": []}
    base = "https://api.etherscan.io/api"
    block_hex = (safe_get_json(base, params={"module": "proxy", "action": "eth_blockNumber", "apikey": CFG.etherscan_key}) or {}).get("result")
    if not block_hex:
        return {"detected": False, "transactions": []}
    block = safe_get_json(base, params={"module": "proxy", "action": "eth_getBlockByNumber", "tag": block_hex, "boolean": "true", "apikey": CFG.etherscan_key}) or {}
    txs = ((block.get("result") or {}).get("transactions") or [])
    whales = []
    for tx in txs:
        try:
            eth = int(tx.get("value", "0x0"), 16) / 10**18
            if eth >= 100:
                whales.append({"hash": tx.get("hash"), "eth": round(eth, 2)})
        except Exception:
            continue
    return {"detected": bool(whales), "transactions": whales[:5]}


def collect_data() -> dict[str, Any]:
    prices = get_coingecko_prices()
    indicators = get_binance_indicators()
    return {
        "timestamp": now_pkt().isoformat(),
        "prices": prices,
        "indicators": indicators,
        "macro_trend": get_macro_trend(),
        "futures": get_futures_data(),
        "fear_greed": get_fear_greed(),
        "news": get_crypto_news(),
        "btc_whales": get_btc_whales(),
        "eth_whales": get_etherscan_whales(),
        "google_trends": get_google_trends(),
        "influencer_alerts": get_influencer_alerts(),
    }


def fallback_analysis(data: dict[str, Any], state: dict[str, Any] | None = None) -> dict[str, Any]:
    """Used only if Groq is unavailable."""
    state = state or load_state()
    btc_rsi = ((data.get("indicators") or {}).get("BTC") or {}).get("rsi") or 50
    news_mood = (data.get("news") or {}).get("mood")
    fng = int((data.get("fear_greed") or {}).get("value") or 50)
    trend = ((data.get("macro_trend") or {}).get("BTC") or {}).get("trend")
    bullish = (btc_rsi < 35) + (news_mood == "BULLISH") + (fng < 25)
    bearish = (btc_rsi > 70) + (news_mood == "BEARISH") + (fng > 75)
    if trend == "Bullish":
        bullish += 1
    if trend == "Bearish":
        bearish += 1
    action = "BUY" if bullish > bearish else "SELL" if bearish > bullish else "HOLD"
    allocation = default_allocation(state)
    if action != "HOLD" and allocation < MIN_TRADE_USD:
        action = "HOLD"
    confidence = 50 if action == "HOLD" else min(85, 55 + max(bullish, bearish) * 10)
    return {
        "action": action,
        "target_coin": "BTC",
        "confidence_score": confidence,
        "entry_zone": format_entry(data, "BTC"),
        "take_profit": target_price(data, "BTC", action, 1.03),
        "stop_loss": target_price(data, "BTC", action, 0.985),
        "allocation_usd": f"{allocation if action != 'HOLD' else 0:.2f}",
        "institutional_logic": "Fallback rule engine used because Groq was unavailable. Signal is conservative and should be treated as high-risk.",
    }


def analyze_with_groq(data: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    if not usable(CFG.groq_api_key):
        return fallback_analysis(data, state)

    system_prompt = f"""
You are a professional multi-billion dollar crypto hedge fund analyst.
Simulate a 3-Agent boardroom debate internally:
- Agent 1 (Technical Chartist): Evaluates 4H trend, 15M RSI/MACD, Funding Rates, and Open Interest.
- Agent 2 (Sentiment & Flow Analyst): Evaluates RSS/NewsAPI news, Whale transactions, and Nitter alerts.
- Agent 3 (Risk Manager): Aligns both agents, uses total capital, active positions, entry zone, and stop loss to size the trade.
Capital rules:
- TOTAL_CAPITAL = ${TOTAL_CAPITAL:.2f}
- Active positions now = {active_position_count(state)}
- Maximum active positions = {MAX_ACTIVE_POSITIONS}
- Allocation must NEVER be less than ${MIN_TRADE_USD:.2f} and never more than ${MAX_TRADE_USD:.2f} per trade.
- If risk is too high for a ${MIN_TRADE_USD:.2f} position, or no slot is available, output HOLD with allocation_usd "0.00".
- If Technical Chartist and Sentiment/Flow Analyst disagree, output HOLD.
Respond ONLY in this exact valid JSON format:
{{
  "action": "BUY or SELL or HOLD",
  "target_coin": "BTC or ETH or SOL",
  "confidence_score": 0,
  "entry_zone": "Price range",
  "take_profit": "Target price",
  "stop_loss": "Strict exit price",
  "allocation_usd": "Exact dollar amount to invest (e.g., 10.00 or 15.00)",
  "institutional_logic": "2 sentences explaining the mathematical and psychological alignment"
}}
""".strip()
    try:
        client = Groq(api_key=CFG.groq_api_key)
        res = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(data, default=str)[:18000]},
            ],
            temperature=0.1,
        )
        return normalize_analysis(parse_json(res.choices[0].message.content), data, state)
    except Exception:
        return fallback_analysis(data, state)


def parse_json(text: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", text or "", flags=re.S)
    return json.loads(match.group(0) if match else "{}")


def normalize_analysis(raw: dict[str, Any], data: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    action = str(raw.get("action", "HOLD")).upper()
    coin = str(raw.get("target_coin", raw.get("best_coin", "BTC"))).upper()
    confidence = int(float(raw.get("confidence_score", raw.get("confidence", 0)) or 0))
    coin = coin if coin in COINS else "BTC"
    allocation = normalize_allocation(raw.get("allocation_usd"), action, state)
    action = "HOLD" if action != "HOLD" and allocation < MIN_TRADE_USD else action
    return {
        "action": action if action in {"BUY", "SELL", "HOLD"} else "HOLD",
        "target_coin": coin,
        "confidence_score": max(0, min(100, confidence)),
        "entry_zone": raw.get("entry_zone") or raw.get("entry_price") or format_entry(data, coin),
        "take_profit": raw.get("take_profit") or target_price(data, coin, action, 1.03),
        "stop_loss": raw.get("stop_loss") or target_price(data, coin, action, 0.985),
        "allocation_usd": f"{allocation if action != 'HOLD' else 0:.2f}",
        "institutional_logic": str(raw.get("institutional_logic") or raw.get("reason") or "No logic returned.")[:500],
    }


def normalize_allocation(value: Any, action: str, state: dict[str, Any]) -> float:
    if action == "HOLD":
        return 0.0
    available = max_available_allocation(state)
    if available < MIN_TRADE_USD:
        return 0.0
    try:
        amount = float(re.sub(r"[^0-9.]", "", str(value)) or 0)
    except Exception:
        amount = MIN_TRADE_USD
    return max(MIN_TRADE_USD, min(MAX_TRADE_USD, available, amount))


def format_entry(data: dict[str, Any], coin: str) -> str:
    price = ((data.get("prices") or {}).get(coin) or {}).get("price")
    if not price:
        return "N/A"
    low, high = price * 0.99, price * 1.01
    return f"${low:,.0f} - ${high:,.0f}"


def parse_price(value: Any) -> float | None:
    """Extract a number from '$67,000 - $68,500' or '$67,000'."""
    nums = [float(n.replace(",", "")) for n in re.findall(r"\d[\d,]*(?:\.\d+)?", str(value))]
    return sum(nums) / len(nums) if nums else None


def current_coin_price(data: dict[str, Any], coin: str) -> float | None:
    price = ((data.get("prices") or {}).get(coin) or {}).get("price")
    try:
        return float(price) if price else None
    except Exception:
        return None


def target_price(data: dict[str, Any], coin: str, action: str, multiplier: float) -> str:
    price = ((data.get("prices") or {}).get(coin) or {}).get("price")
    if not price:
        return "N/A"
    if action == "SELL":
        multiplier = 2 - multiplier
    return f"${price * multiplier:,.0f}"


def market_line(data: dict[str, Any], coin: str = "BTC") -> dict[str, str]:
    fng = data.get("fear_greed") or {}
    coin_indicators = ((data.get("indicators") or {}).get(coin) or {})
    news = data.get("news") or {}
    whales = bool((data.get("btc_whales") or {}).get("detected") or (data.get("eth_whales") or {}).get("detected"))
    trends = data.get("google_trends") or {}
    macro = ((data.get("macro_trend") or {}).get(coin) or {}).get("trend", "Unknown")
    futures = ((data.get("futures") or {}).get(coin) or {})
    return {
        "fear": f"{fng.get('value', 'N/A')} ({fng.get('label', 'Unknown')})",
        "rsi": f"{coin_indicators.get('rsi', 'N/A')} ({rsi_label(coin_indicators.get('rsi'))})",
        "news": f"{news.get('mood', 'UNKNOWN')} ({news.get('positive', 0)}/{len(news.get('items', []))} positive)",
        "whales": "DETECTED 🐋" if whales else "Not detected",
        "trends": "SPIKING 📈" if trends.get("status") == "SPIKING" else trends.get("status", "UNKNOWN"),
        "macro": macro,
        "funding": futures.get("funding_label", "Unknown"),
    }


def rsi_label(value: Any) -> str:
    try:
        value = float(value)
        return "Oversold" if value <= 30 else "Overbought" if value >= 70 else "Neutral"
    except Exception:
        return "Unknown"


def build_alert(data: dict[str, Any], analysis: dict[str, Any]) -> str:
    coin = analysis["target_coin"]
    lines = market_line(data, coin)
    return f"""⚡ INTEL SIGNAL: {coin} ({analysis['action']})
━━━━━━━━━━━━━━━━━
🎯 Strategy: {analysis['action']}
💰 Position Size: Invest exactly ${analysis['allocation_usd']} USDT
🔥 Confidence: {analysis['confidence_score']}%
🛑 Stop Loss: {analysis['stop_loss']} | 📈 Take Profit: {analysis['take_profit']}
━━━━━━━━━━━━━━━━━
🧠 Institutional Logic: {analysis['institutional_logic']}
━━━━━━━━━━━━━━━━━
📊 Core Metrics Matrix:
- 4H Macro Trend: {lines['macro']}
- 15M RSI: {lines['rsi']}
- Funding Rate: {lines['funding']}
- Whale Activity: {lines['whales']}
- Market Sentiment: {lines['fear']}
━━━━━━━━━━━━━━━━━
⚠️ Strict Risk Rule: Never risk more than 2% of capital."""


def send_telegram(text: str) -> bool:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram skipped: TELEGRAM_TOKEN or TELEGRAM_CHAT_ID missing.")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        res = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text}, timeout=20)
        if res.status_code >= 400:
            print(f"Telegram error {res.status_code}: {res.text[:300]}")
        return res.status_code < 400
    except Exception as err:
        print(f"Telegram exception: {err}")
        return False


def test_telegram() -> None:
    token = os.environ.get("TELEGRAM_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    print(f"DEBUG token length: {len(token)}")
    print(f"DEBUG chat_id length: {len(chat_id)}")
    print(f"DEBUG token empty: {token == ''}")
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        raise RuntimeError(
            "Telegram test failed. TELEGRAM_TOKEN and TELEGRAM_CHAT_ID must be set in GitHub Secrets."
        )
    ok = send_telegram("✅ CryptoMind GitHub Actions connected.")
    if not ok:
        raise RuntimeError("Telegram test failed. Check TELEGRAM_TOKEN and TELEGRAM_CHAT_ID secrets.")
    print("Telegram test sent.")


def should_send_signal(analysis: dict[str, Any], state: dict[str, Any], data: dict[str, Any]) -> bool:
    if analysis["confidence_score"] <= 65:
        return False
    if analysis["action"] not in {"BUY", "SELL"}:
        return False
    coin = analysis["target_coin"]
    rsi = indicator_value(data, coin, "rsi")
    fear = fear_greed_value(data)
    if analysis["action"] == "BUY" and not (rsi is not None and rsi < 40 and fear is not None and fear < 40):
        return False
    if analysis["action"] == "SELL" and not (rsi is not None and rsi > 60 and fear is not None and fear > 60):
        return False
    if analysis["action"] != "HOLD" and len(ACTIVE_TRADES) >= MAX_ACTIVE_POSITIONS:
        send_telegram("⏸️ Max positions reached. Waiting for exit.")
        return False
    if analysis["action"] != "HOLD" and float(analysis.get("allocation_usd") or 0) < MIN_TRADE_USD:
        return False
    return not duplicate_coin_signal_within(state, coin, hours=4)


def indicator_value(data: dict[str, Any], coin: str, key: str) -> float | None:
    try:
        value = ((data.get("indicators") or {}).get(coin) or {}).get(key)
        return float(value) if value is not None else None
    except Exception:
        return None


def fear_greed_value(data: dict[str, Any]) -> float | None:
    try:
        value = (data.get("fear_greed") or {}).get("value")
        return float(value) if value is not None else None
    except Exception:
        return None


def duplicate_coin_signal_within(state: dict[str, Any], coin: str, hours: int) -> bool:
    last_by_coin = state.setdefault("last_signal_by_coin", {})
    last_time = last_by_coin.get(coin)
    if not last_time:
        return False
    try:
        return now_pkt() - datetime.fromisoformat(last_time) < timedelta(hours=hours)
    except Exception:
        return False


def log_signal(data: dict[str, Any], analysis: dict[str, Any], sent: bool) -> None:
    row = {"time": now_pkt().isoformat(), "sent": sent, "analysis": analysis, "data": data}
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def record_signal_history(analysis: dict[str, Any]) -> int:
    rows = load_history()
    row_id = next_history_id(rows)
    rows.append({
        "id": row_id,
        "timestamp": now_pkt().strftime("%Y-%m-%d %H:%M"),
        "coin": analysis["target_coin"],
        "action": analysis["action"],
        "entry_price": parse_price(analysis["entry_zone"]),
        "take_profit": parse_price(analysis["take_profit"]),
        "stop_loss": parse_price(analysis["stop_loss"]),
        "allocation_usd": float(analysis.get("allocation_usd") or 0),
        "confidence": int(analysis.get("confidence_score") or 0),
        "result": "PENDING",
        "pnl_usd": 0.00,
        "closed_at": None,
        "paper_mode": PAPER_MODE,
    })
    save_history(rows)
    return row_id


def close_history_trade(history_id: int | None, result: str, pnl: float) -> None:
    if not history_id:
        return
    rows = load_history()
    for row in rows:
        if row.get("id") == history_id:
            row["result"] = result
            row["pnl_usd"] = round(pnl, 2)
            row["closed_at"] = now_pkt().strftime("%Y-%m-%d %H:%M")
            break
    save_history(rows)


def record_active_position(state: dict[str, Any], analysis: dict[str, Any], history_id: int | None = None) -> None:
    if analysis["action"] != "BUY":
        return
    positions = state.setdefault("active_positions", {})
    entry_price = parse_price(analysis["entry_zone"])
    positions[analysis["target_coin"]] = {
        "coin": analysis["target_coin"],
        "entry_price": entry_price,
        "allocation_usd": analysis["allocation_usd"],
        "stop_loss": parse_price(analysis["stop_loss"]),
        "take_profit": parse_price(analysis["take_profit"]),
        "history_id": history_id,
        "timestamp": now_pkt().isoformat(),
    }
    sync_active_trades(state)


def check_active_trades(state: dict[str, Any], data: dict[str, Any]) -> None:
    """Close BUY trades when TP/SL is touched. P&L is shown in dollars."""
    positions = state.setdefault("active_positions", {})
    for coin, trade in list(positions.items()):
        try:
            if not isinstance(trade, dict):
                positions.pop(coin, None)
                continue
            current = current_coin_price(data, coin)
            entry = float(trade.get("entry_price") or parse_price(trade.get("entry_zone")) or 0)
            stop = float(trade.get("stop_loss") or 0)
            take = float(trade.get("take_profit") or 0)
            allocation = float(trade.get("allocation_usd") or 0)
            if not current or not entry or not allocation:
                continue
            if take and current >= take:
                pnl = ((current - entry) / entry) * allocation
                send_telegram(f"✅ PROFIT HIT — {coin} closed\nP&L: ${pnl:.2f}")
                close_history_trade(trade.get("history_id"), "WIN", pnl)
                positions.pop(coin, None)
            elif stop and current <= stop:
                pnl = ((current - entry) / entry) * allocation
                send_telegram(f"🛑 STOP LOSS HIT — {coin} closed\nP&L: ${pnl:.2f}")
                close_history_trade(trade.get("history_id"), "LOSS", pnl)
                positions.pop(coin, None)
        except Exception:
            continue
    sync_active_trades(state)


def ensure_persisted_files() -> None:
    """GitHub Actions commits these files after each run; git add fails if any is missing."""
    if not HISTORY_FILE.exists():
        save_history([])
    if not LOG_FILE.exists():
        LOG_FILE.touch()
    if not STATE_FILE.exists():
        save_state(load_state())


def run_analysis() -> None:
    try:
        ensure_persisted_files()
        state = reset_daily_if_needed(load_state())
        data = collect_data()
        check_active_trades(state, data)
        analysis = analyze_with_groq(data, state)
        sent = False

        if should_send_signal(analysis, state, data):
            sent = send_telegram(build_alert(data, analysis))
            if sent:
                history_id = record_signal_history(analysis)
                state["daily_count"] = int(state.get("daily_count", 0)) + 1
                state["last_signal"] = f"{analysis['action']}:{analysis['target_coin']}"
                state["last_signal_time"] = now_pkt().isoformat()
                state.setdefault("last_signal_by_coin", {})[analysis["target_coin"]] = now_pkt().isoformat()
                record_active_position(state, analysis, history_id)

        log_signal(data, analysis, sent)
        save_state(state)
        print(f"[{now_pkt():%Y-%m-%d %H:%M:%S}] Done. {analysis['action']} {analysis['target_coin']} {analysis['confidence_score']}%. Sent={sent}")
    except Exception as err:
        print(f"[{now_pkt():%Y-%m-%d %H:%M:%S}] Error skipped: {err}")
    try:
        maybe_send_scheduled_reports()
    except Exception as err:
        print(f"[{now_pkt():%Y-%m-%d %H:%M:%S}] Scheduled reports skipped: {err}")


def maybe_send_scheduled_reports() -> None:
    """Deliver daily/weekly reports from the 15-min cron once they are due."""
    now = now_pkt()
    state = load_state()
    today = now.date().isoformat()
    if now.hour >= 8 and state.get("last_daily_report") != today:
        daily_summary()
        state = load_state()
        state["last_daily_report"] = today
        save_state(state)
    week = now.strftime("%G-W%V")
    if now.weekday() == 0 and now.hour >= 9 and state.get("last_weekly_report") != week:
        weekly_summary()
        state = load_state()
        state["last_weekly_report"] = week
        save_state(state)


def daily_summary() -> None:
    try:
        state = reset_daily_if_needed(load_state())
        send_telegram(build_performance_report())
        maybe_send_live_mode_reminder(state)
        save_state(state)
    except Exception as err:
        print(f"[{now_pkt():%Y-%m-%d %H:%M:%S}] Daily summary skipped: {err}")


def build_performance_report() -> str:
    rows = load_history()
    today = now_pkt().date().isoformat()
    today_rows = [r for r in rows if str(r.get("timestamp", "")).startswith(today)]
    today_closed = [r for r in rows if str(r.get("closed_at", "")).startswith(today)]
    wins = [r for r in today_closed if r.get("result") == "WIN"]
    losses = [r for r in today_closed if r.get("result") == "LOSS"]
    closed = wins + losses
    win_rate = round((len(wins) / len(closed)) * 100) if closed else 0
    pnl_today = sum(float(r.get("pnl_usd") or 0) for r in today_closed)
    pnl_total = sum(float(r.get("pnl_usd") or 0) for r in rows)
    best = max(today_closed, key=lambda r: float(r.get("pnl_usd") or 0), default=None)
    worst = min(today_closed, key=lambda r: float(r.get("pnl_usd") or 0), default=None)
    accuracy_7d = seven_day_win_rate(rows)
    return f"""📊 DAILY PERFORMANCE REPORT
━━━━━━━━━━━━━━━━━
📅 Date: {today}
━━━━━━━━━━━━━━━━━
Total Signals Today: {len(today_rows)}
✅ Wins: {len(wins)}  🛑 Losses: {len(losses)}
Win Rate: {win_rate}%
━━━━━━━━━━━━━━━━━
💰 Paper P&L Today: {money(pnl_today)}
💰 Paper P&L Total: {money(pnl_total)}
━━━━━━━━━━━━━━━━━
Best Signal: {format_signal_result(best)}
Worst Signal: {format_signal_result(worst)}
━━━━━━━━━━━━━━━━━
📈 Bot Accuracy: {accuracy_7d}% (last 7 days)"""


def money(value: float) -> str:
    sign = "+" if value >= 0 else "-"
    return f"{sign}${abs(value):.2f}"


def format_signal_result(row: dict[str, Any] | None) -> str:
    if not row:
        return "N/A"
    return f"{row.get('coin', 'N/A')} {money(float(row.get('pnl_usd') or 0))}"


def seven_day_win_rate(rows: list[dict[str, Any]]) -> int:
    cutoff = now_pkt() - timedelta(days=7)
    recent = []
    for row in rows:
        try:
            closed_at = parsed_history_time(row.get("closed_at"))
            if closed_at and closed_at >= cutoff:
                recent.append(row)
        except Exception:
            continue
    closed = [r for r in recent if r.get("result") in {"WIN", "LOSS"}]
    wins = [r for r in closed if r.get("result") == "WIN"]
    return round((len(wins) / len(closed)) * 100) if closed else 0


def maybe_send_live_mode_reminder(state: dict[str, Any]) -> None:
    last = state.get("last_winrate_reminder")
    if last and now_pkt() - datetime.fromisoformat(last) < timedelta(days=7):
        return
    rate = seven_day_win_rate(load_history())
    send_telegram(f"📋 7-day win rate: {rate}%.\nMinimum 60% required for live trading.")
    state["last_winrate_reminder"] = now_pkt().isoformat()


def weekly_summary() -> None:
    try:
        send_telegram(build_weekly_report())
    except Exception as err:
        print(f"[{now_pkt():%Y-%m-%d %H:%M:%S}] Weekly summary skipped: {err}")


def build_weekly_report() -> str:
    rows = load_history()
    cutoff = now_pkt() - timedelta(days=7)
    week_signals = [r for r in rows if (t := parsed_history_time(r.get("timestamp"))) and t >= cutoff]
    week_closed = [r for r in rows if (t := parsed_history_time(r.get("closed_at"))) and t >= cutoff]
    wins = [r for r in week_closed if r.get("result") == "WIN"]
    losses = [r for r in week_closed if r.get("result") == "LOSS"]
    closed = wins + losses
    win_rate = round((len(wins) / len(closed)) * 100) if closed else 0
    net_pnl = sum(float(r.get("pnl_usd") or 0) for r in week_closed)
    coin_pnl = pnl_by_coin(week_closed)
    best_coin = max(coin_pnl, key=coin_pnl.get) if coin_pnl else "N/A"
    worst_coin = min(coin_pnl, key=coin_pnl.get) if coin_pnl else "N/A"
    ready = "YES" if win_rate >= 60 and closed else "NO"
    return f"""📊 WEEKLY REPORT
Total Signals: {len(week_signals)} | Wins: {len(wins)} | Losses: {len(losses)}
Win Rate: {win_rate}% | Net P&L: {money(net_pnl)}
Best Coin: {best_coin} | Worst Coin: {worst_coin}
Ready for live trading: {ready} (60% threshold)"""


def parsed_history_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(str(value), "%Y-%m-%d %H:%M").replace(tzinfo=PKT)
    except Exception:
        return None


def pnl_by_coin(rows: list[dict[str, Any]]) -> dict[str, float]:
    out: dict[str, float] = {}
    for row in rows:
        coin = row.get("coin", "N/A")
        out[coin] = out.get(coin, 0.0) + float(row.get("pnl_usd") or 0)
    return out


def read_today_logs() -> list[dict[str, Any]]:
    today = now_pkt().date().isoformat()
    if not LOG_FILE.exists():
        return []
    rows = []
    for line in LOG_FILE.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
            if str(row.get("time", "")).startswith(today):
                rows.append(row)
        except Exception:
            continue
    return rows


def best_performing_coin(rows: list[dict[str, Any]]) -> str:
    latest = (rows[-1].get("data", {}).get("prices", {}) if rows else {}) or {}
    changes = {coin: meta.get("change_24h") for coin, meta in latest.items() if meta.get("change_24h") is not None}
    return max(changes, key=changes.get) if changes else "N/A"


def market_mood_score(rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 5
    last = rows[-1]
    action = last.get("analysis", {}).get("action")
    confidence = int(last.get("analysis", {}).get("confidence_score") or last.get("analysis", {}).get("confidence") or 50)
    base = 7 if action == "BUY" else 3 if action == "SELL" else 5
    return max(1, min(10, round((base + confidence / 10) / 2)))


def start_scheduler(run_immediately: bool = False) -> None:
    global SCHEDULER
    if SCHEDULER and SCHEDULER.running:
        return
    SCHEDULER = BackgroundScheduler(timezone=PKT)
    SCHEDULER.add_job(run_analysis, "interval", minutes=15, id="analysis", max_instances=1, coalesce=True)
    SCHEDULER.add_job(daily_summary, "cron", hour=8, minute=0, id="daily_summary")
    SCHEDULER.add_job(weekly_summary, "cron", day_of_week="mon", hour=9, minute=0, id="weekly_summary")
    SCHEDULER.start()
    print("CryptoMind scheduler started.")
    if run_immediately:
        run_analysis()


def main() -> None:
    print("CryptoMind bot started. Press CTRL+C to stop.")
    start_scheduler(run_immediately=True)

    while True:
        time.sleep(60)


if __name__ == "__main__":
    main()
