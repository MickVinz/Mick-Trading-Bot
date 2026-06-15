"""
BingX REST-API-Wrapper — NUR LESENDE Endpunkte (Paper Trading).

Zweck: BTC/USDT-Perpetual-Kerzen und Preise von BingX laden, im EXAKT
gleichen DataFrame-Format wie fetch_binance_klines() in
scripts/verify_indicators.py. Dadurch arbeiten alle bestehenden Module
(indicators, setup_detector, trade_levels) OHNE Änderung weiter.

WICHTIG: In diesem Schritt werden KEINE Orders gesendet, kein echtes
Trading. Der Kontostand-Endpunkt ist vorbereitet (API-Key-Signierung),
wird aber noch nicht genutzt.

-----------------------------------------------------------------------------
UNTERSCHIEDE BINGX vs. BINANCE (für spätere Wartung dokumentiert)
-----------------------------------------------------------------------------
1. Symbol-Format:   BingX "BTC-USDT" (mit Bindestrich)
                    Binance "BTCUSDT" (ohne).
2. Antwort-Hülle:   BingX verpackt alles in {"code":0,"msg":"","data":...}.
                    code != 0 ist ein API-Fehler (msg enthält den Grund).
                    Binance liefert die Nutzdaten direkt.
3. Klines-Struktur: BingX liefert je Kerze ein OBJEKT mit benannten Feldern
                    {"open","close","high","low","volume","time"}.
                    Binance liefert ein ARRAY mit festen Positionen
                    [open_time, open, high, low, close, volume, close_time, ...].
4. Sortierung:      BingX-Klines kommen NEUESTE ZUERST (absteigend).
                    Wir drehen sie auf chronologisch aufsteigend (wie Binance).
5. Schlusszeit:     BingX liefert nur "time" (= Öffnungszeit der Kerze),
                    KEINE close_time. Für den Repaint-Schutz berechnen wir
                    close_time = time + Intervalldauer selbst.
-----------------------------------------------------------------------------
"""

import hashlib
import hmac
import os
import time as _time
from pathlib import Path
from urllib.parse import urlencode

import pandas as pd
import requests
from dotenv import load_dotenv

# .env aus dem Projekt-Root laden (zwei Ebenen über dieser Datei: src/exchange/).
# In diesem Schritt werden die Keys nur GELADEN, nicht verwendet — nur Struktur.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
# encoding="utf-8-sig" entfernt ein evtl. vorhandenes BOM (z.B. wenn die .env
# mit PowerShells `Out-File -Encoding utf8` erstellt wurde). Ohne das landet
# das BOM im Namen der ERSTEN Variable (﻿BINGX_API_KEY) -> lädt als leer.
load_dotenv(_PROJECT_ROOT / ".env", encoding="utf-8-sig")

BINGX_API_KEY = os.getenv("BINGX_API_KEY", "")
BINGX_SECRET_KEY = os.getenv("BINGX_SECRET_KEY", "")

BASE_URL = "https://open-api.bingx.com"

# Intervall-String -> Dauer in Millisekunden (für close_time-Berechnung).
_INTERVAL_MS = {
    "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000,
    "30m": 1_800_000, "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000,
    "6h": 21_600_000, "8h": 28_800_000, "12h": 43_200_000, "1d": 86_400_000,
}


def _get(path: str, params: dict, timeout: int = 15) -> dict:
    """
    Führt ein öffentliches GET aus und prüft die BingX-Antwort-Hülle.

    Wirft requests.HTTPError bei HTTP-Fehlern und RuntimeError, wenn die
    API zwar HTTP 200 liefert, aber im Body code != 0 meldet.
    """
    response = requests.get(BASE_URL + path, params=params, timeout=timeout)
    response.raise_for_status()
    payload = response.json()

    # BingX-Konvention: code == 0 bedeutet Erfolg.
    if payload.get("code", 0) != 0:
        raise RuntimeError(
            f"BingX-API-Fehler (code={payload.get('code')}): "
            f"{payload.get('msg', 'kein Grund angegeben')}"
        )
    return payload


def fetch_bingx_klines(
    symbol: str = "BTC-USDT",
    interval: str = "5m",
    limit: int = 500,
    drop_unclosed: bool = True,
) -> pd.DataFrame:
    """
    Holt die letzten `limit` Kerzen vom öffentlichen BingX-Swap-Endpunkt.

    Endpunkt: GET /openApi/swap/v2/quote/klines  (kein API-Key nötig)

    Rückgabe: pandas DataFrame mit IDENTISCHEM Format wie
    fetch_binance_klines() — Spalten in dieser Reihenfolge:
        timestamp (UTC, tz-aware), open, high, low, close, volume (alle float)
    chronologisch aufsteigend, Index zurückgesetzt.

    REPAINT-SCHUTZ (Architektur-Regel, Spec Abschnitt 9):
    Wie bei Binance wird standardmäßig (drop_unclosed=True) jede Kerze
    verworfen, deren Schlusszeit noch in der Zukunft liegt. Da BingX keine
    close_time liefert, berechnen wir sie aus time + Intervalldauer.
    drop_unclosed=False nur für reine Anzeige-/Debug-Zwecke verwenden.
    """
    payload = _get(
        "/openApi/swap/v2/quote/klines",
        {"symbol": symbol, "interval": interval, "limit": limit},
    )
    raw = payload["data"]  # Liste von Kerzen-Objekten, NEUESTE ZUERST

    if not raw:
        raise RuntimeError(
            f"BingX lieferte keine Kerzen für {symbol} / {interval}."
        )

    df = pd.DataFrame(raw)

    # BingX nennt das Feld "time" (= Öffnungszeit der Kerze, Unix-Millisekunden).
    df["open_time"] = df["time"].astype("int64")
    df["timestamp"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)

    for col in ("open", "high", "low", "close", "volume"):
        df[col] = df[col].astype(float)

    # Unterschied zu Binance: BingX liefert keine close_time -> selbst berechnen.
    interval_ms = _INTERVAL_MS.get(interval)
    if interval_ms is None:
        raise ValueError(f"Unbekanntes Intervall '{interval}' für close_time-Berechnung.")
    df["close_time"] = pd.to_datetime(df["open_time"] + interval_ms, unit="ms", utc=True)

    # Unterschied zu Binance: BingX kommt absteigend -> chronologisch sortieren.
    df = df.sort_values("timestamp").reset_index(drop=True)

    # Repaint-Schutz: noch nicht geschlossene Kerzen verwerfen.
    if drop_unclosed:
        now = pd.Timestamp.now(tz="utc")
        df = df[df["close_time"] <= now]

    return df[["timestamp", "open", "high", "low", "close", "volume"]].reset_index(drop=True)


def fetch_bingx_klines_range(
    symbol: str,
    interval: str,
    start_dt: "pd.Timestamp",
    end_dt: "pd.Timestamp",
    verbose: bool = False,
) -> "pd.DataFrame":
    """
    Lädt BingX-Kerzen für einen historischen Zeitraum (paginiert).

    Macht so viele GET-Anfragen wie nötig (je max. 1000 Kerzen), bis
    start_dt..end_dt vollständig abgedeckt ist. Gibt einen chronologisch
    sortierten DataFrame im gleichen Format wie fetch_bingx_klines() zurück.

    symbol    : Format "BTC-USDT" (BingX-Stil, Bindestrich).
    start_dt  : UTC-aware pd.Timestamp (inklusive).
    end_dt    : UTC-aware pd.Timestamp (exklusive — laufende Kerze wird verworfen).
    verbose   : True = Fortschritts-Prints alle 10 Seiten.
    """
    import time as _sleep_mod

    interval_ms = _INTERVAL_MS.get(interval)
    if interval_ms is None:
        raise ValueError(f"Unbekanntes Intervall '{interval}'.")

    cursor_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)
    frames = []
    page = 0

    while cursor_ms < end_ms:
        payload = _get("/openApi/swap/v2/quote/klines", {
            "symbol": symbol,
            "interval": interval,
            "limit": 1000,
            "startTime": cursor_ms,
        })
        raw = payload.get("data", [])
        if not raw:
            break

        df = pd.DataFrame(raw)
        df["timestamp"] = pd.to_datetime(df["time"].astype("int64"), unit="ms", utc=True)
        for col in ("open", "high", "low", "close", "volume"):
            df[col] = df[col].astype(float)
        df = df.sort_values("timestamp").reset_index(drop=True)

        # Kerzen außerhalb des Zielbereichs abschneiden
        df = df[df["timestamp"] < end_dt]
        if df.empty:
            break
        frames.append(df[["timestamp", "open", "high", "low", "close", "volume"]])

        last_open_ms = int(df.iloc[-1]["timestamp"].timestamp() * 1000)
        cursor_ms = last_open_ms + interval_ms
        page += 1

        if verbose and page % 10 == 0:
            total_span = end_ms - int(start_dt.timestamp() * 1000)
            done = cursor_ms - int(start_dt.timestamp() * 1000)
            pct = min(100.0, done / total_span * 100)
            print(f"    {symbol}: {pct:.0f}% ({page} Seiten)...", flush=True)

        if len(raw) < 1000:
            break

        _sleep_mod.sleep(0.15)  # Rate Limit

    if not frames:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

    result = pd.concat(frames, ignore_index=True)
    result = (result
              .drop_duplicates("timestamp")
              .sort_values("timestamp")
              .reset_index(drop=True))
    return result


def fetch_bingx_price(symbol: str = "BTC-USDT") -> float:
    """
    Holt den aktuellen Preis (Mark/Last) vom öffentlichen BingX-Endpunkt.

    Endpunkt: GET /openApi/swap/v2/quote/price  (kein API-Key nötig)

    Rückgabe: aktueller Preis als float.
    """
    payload = _get("/openApi/swap/v2/quote/price", {"symbol": symbol})
    return float(payload["data"]["price"])


def _sign(query_string: str) -> str:
    """
    Erzeugt die HMAC-SHA256-Signatur für private Endpunkte.

    BingX signiert den vollständigen Query-String (inkl. timestamp) mit dem
    Secret-Key. NUR Vorbereitung — in diesem Schritt nicht aufgerufen.
    """
    return hmac.new(
        BINGX_SECRET_KEY.encode("utf-8"),
        query_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def fetch_bingx_balance() -> dict:
    """
    Holt den Kontostand (PRIVATER Endpunkt, braucht API-Key + Signatur).

    Endpunkt: GET /openApi/swap/v2/user/balance

    ACHTUNG: Vorbereitet, aber in diesem Schritt NOCH NICHT genutzt
    (Paper Trading, keine echten Orders/Konten). Erst beim Live-Schritt
    aktivieren, sobald gültige Keys in der .env hinterlegt sind.

    Rückgabe: das 'data'-Objekt der BingX-Antwort.
    """
    if not BINGX_API_KEY or not BINGX_SECRET_KEY:
        raise RuntimeError(
            "Kein BINGX_API_KEY / BINGX_SECRET_KEY gesetzt. "
            "Keys in die .env eintragen (Vorlage: .env.example)."
        )

    # timestamp in Millisekunden ist für signierte BingX-Requests Pflicht.
    params = {"timestamp": int(_time.time() * 1000)}
    query_string = urlencode(params)
    params["signature"] = _sign(query_string)

    headers = {"X-BX-APIKEY": BINGX_API_KEY}
    response = requests.get(
        BASE_URL + "/openApi/swap/v2/user/balance",
        params=params, headers=headers, timeout=15,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("code", 0) != 0:
        raise RuntimeError(
            f"BingX-API-Fehler (code={payload.get('code')}): "
            f"{payload.get('msg', 'kein Grund angegeben')}"
        )
    return payload["data"]
