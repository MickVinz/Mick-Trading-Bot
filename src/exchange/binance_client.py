"""
Binance Public API — OHLCV-Daten ohne API-Key.

Vorteile gegenüber BingX:
  - 1m: bis zu ~1000 Tage History
  - 5m, 15m, 1H, 4H: mehrere Jahre
  - Kein Rate-Limit-Problem bei normalem Backtest-Betrieb
"""

import time

import pandas as pd
import requests

_BASE = "https://api.binance.com"

_INTERVAL_MS = {
    "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000,
    "30m": 1_800_000, "1h": 3_600_000, "2h": 7_200_000,
    "4h": 14_400_000, "6h": 21_600_000, "12h": 43_200_000, "1d": 86_400_000,
}


def _to_binance_symbol(symbol: str) -> str:
    """BTC/USDT oder BTC-USDT → BTCUSDT"""
    return symbol.replace("/", "").replace("-", "")


def fetch_binance_klines_range(
    symbol: str,
    interval: str,
    start_dt,
    end_dt,
    verbose: bool = False,
) -> pd.DataFrame:
    """
    Laedt alle OHLCV-Kerzen von Binance im angegebenen Zeitraum.

    symbol:   "BTC/USDT", "BTC-USDT" oder "BTCUSDT"
    interval: "1m", "5m", "15m", "30m", "1h", "4h" ...
    start_dt / end_dt: pd.Timestamp (UTC) oder datetime-aehnlich

    Gibt DataFrame mit Spalten [timestamp, open, high, low, close, volume] zurueck.
    timestamp ist UTC-aware pd.Timestamp.
    """
    binance_sym = _to_binance_symbol(symbol)
    start_ms = int(pd.Timestamp(start_dt).value // 1_000_000)
    end_ms = int(pd.Timestamp(end_dt).value // 1_000_000)

    ims = _INTERVAL_MS.get(interval, 60_000)
    total_est = max(1, (end_ms - start_ms) // ims)

    all_rows: list = []
    current_start = start_ms
    page = 0
    retries = 3

    while current_start < end_ms:
        params = {
            "symbol": binance_sym,
            "interval": interval,
            "startTime": current_start,
            "endTime": end_ms - 1,
            "limit": 1000,
        }

        for attempt in range(retries):
            try:
                resp = requests.get(
                    f"{_BASE}/api/v3/klines", params=params, timeout=30
                )
                resp.raise_for_status()
                data = resp.json()
                break
            except Exception as exc:
                if attempt == retries - 1:
                    raise
                time.sleep(1.5 * (attempt + 1))

        if not data:
            break

        all_rows.extend(data)
        page += 1

        if verbose and page % 75 == 0:
            pct = min(99, int(len(all_rows) / total_est * 100))
            print(f"    {binance_sym}: {pct}% ({len(all_rows)} Kerzen)...", flush=True)

        last_open_ms = data[-1][0]
        current_start = last_open_ms + ims

        if len(data) < 1000:
            break

    if not all_rows:
        return pd.DataFrame()

    df = pd.DataFrame(all_rows, columns=[
        "timestamp", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades",
        "taker_buy_base", "taker_buy_quote", "ignore",
    ])

    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = df[col].astype(float)

    # Nur Kerzen innerhalb [start_dt, end_dt)
    df = df[df["timestamp"] < pd.Timestamp(end_dt)].reset_index(drop=True)

    return df[["timestamp", "open", "high", "low", "close", "volume"]]
