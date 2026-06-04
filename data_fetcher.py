from __future__ import annotations

import os
import time
from pathlib import Path

import ccxt
import pandas as pd
import requests


class DataFetcher:
    def __init__(self, exchange_id: str = "binance"):
        self.exchange = getattr(ccxt, exchange_id)({"enableRateLimit": True, "options": {"defaultType": "future"}})
        self.cache_dir = Path("data/cache")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = 900

    def _cache_path(self, key: str) -> Path:
        return self.cache_dir / f"{key.replace('/', '_').replace(':', '_')}.parquet"

    def _cached(self, key: str):
        path = self._cache_path(key)
        return pd.read_parquet(path) if path.exists() and time.time() - path.stat().st_mtime < self.ttl_seconds else None

    def _save(self, key: str, df: pd.DataFrame) -> pd.DataFrame:
        df.to_parquet(self._cache_path(key))
        return df

    def _retry(self, fn, fallback=None):
        for i in range(2):
            try:
                return fn()
            except Exception:
                if i == 0:
                    time.sleep(1)
        return fallback

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 500) -> pd.DataFrame:
        key = f"ohlcv_{symbol}_{timeframe}_{limit}"
        cached = self._cached(key)
        if cached is not None:
            return cached
        data = self._retry(lambda: self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit), [])
        df = pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close", "volume"])
        if df.empty:
            return df
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df["symbol"] = symbol
        return self._save(key, df.set_index("timestamp"))

    def fetch_ohlcv_since(self, symbol: str, timeframe: str, since: str, until: str | None = None) -> pd.DataFrame:
        since_ts = pd.Timestamp(since, tz="UTC").value // 1_000_000
        until_ts = pd.Timestamp(until, tz="UTC").value // 1_000_000 if until else pd.Timestamp.now(tz="UTC").value // 1_000_000
        key = f"ohlcv_{symbol}_{timeframe}_{since_ts}_{until_ts}"
        cached = self._cached(key)
        if cached is not None:
            return cached
        rows, cursor = [], since_ts
        while cursor < until_ts:
            batch = self._retry(lambda c=cursor: self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=c, limit=1000), [])
            if not batch:
                break
            rows.extend(batch)
            cursor = batch[-1][0] + self.exchange.parse_timeframe(timeframe) * 1000
            if len(batch) < 1000:
                break
        df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"]).drop_duplicates("timestamp")
        if df.empty:
            return df
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df["symbol"] = symbol
        return self._save(key, df.set_index("timestamp").loc[:pd.Timestamp(until_ts, unit="ms", tz="UTC")])

    def fetch_funding_rate(self, symbol: str) -> float:
        key = f"funding_{symbol}"
        cached = self._cached(key)
        if cached is not None and not cached.empty:
            return float(cached["value"].iloc[-1])
        val = self._retry(lambda: self.exchange.fetch_funding_rate(symbol)["fundingRate"], 0.0)
        self._save(key, pd.DataFrame({"value": [float(val)], "timestamp": [pd.Timestamp.now(tz="UTC")]}))
        return float(val)

    def fetch_open_interest(self, symbol: str) -> float:
        key = f"oi_{symbol}"
        cached = self._cached(key)
        if cached is not None and not cached.empty:
            return float(cached["value"].iloc[-1])
        val = self._retry(lambda: self.exchange.fetch_open_interest(symbol)["openInterestAmount"], 0.0)
        self._save(key, pd.DataFrame({"value": [float(val)], "timestamp": [pd.Timestamp.now(tz="UTC")]}))
        return float(val)

    def fetch_whale_btc(self) -> float:
        key = "whale_btc"
        cached = self._cached(key)
        if cached is not None and not cached.empty:
            return float(cached["value"].iloc[-1])
        data = self._retry(lambda: requests.get("https://mempool.space/api/mempool", timeout=10).json(), {})
        val = float(data.get("total_fee", 0)) / 1e8
        self._save(key, pd.DataFrame({"value": [val], "timestamp": [pd.Timestamp.now(tz="UTC")]}))
        return val

    def fetch_whale_eth(self) -> float:
        key = "whale_eth"
        cached = self._cached(key)
        if cached is not None and not cached.empty:
            return float(cached["value"].iloc[-1])
        api_key = os.getenv("ETHERSCAN_API_KEY", "")
        if not api_key:
            self._save(key, pd.DataFrame({"value": [0.0], "timestamp": [pd.Timestamp.now(tz="UTC")]}))
            return 0.0
        url = f"https://api.etherscan.io/v2/api?chainid=1&module=proxy&action=eth_blockNumber&apikey={api_key}"
        data = self._retry(lambda: requests.get(url, timeout=10).json(), {})
        result = str(data.get("result", ""))
        val = int(result, 16) / 1e9 if result.startswith("0x") else 0.0
        self._save(key, pd.DataFrame({"value": [val], "timestamp": [pd.Timestamp.now(tz="UTC")]}))
        return float(val)

    def fetch_fear_greed(self) -> int:
        key = "fear_greed"
        cached = self._cached(key)
        if cached is not None and not cached.empty:
            return int(cached["value"].iloc[-1])
        data = self._retry(lambda: requests.get("https://api.alternative.me/fng/?limit=1", timeout=10).json(), {"data": [{"value": 50}]})
        val = int(data["data"][0]["value"])
        self._save(key, pd.DataFrame({"value": [val], "timestamp": [pd.Timestamp.now(tz="UTC")]}))
        return val
