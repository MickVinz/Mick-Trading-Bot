"""
Dukascopy-Datafeed — historische OHLCV-Daten ohne API-Key.

Zweck: Daten fuer NICHT-Krypto-Werte (Gold, Aktien-Indizes) holen, die es
auf Binance/BingX nicht gibt. Liefert das EXAKT gleiche DataFrame-Format wie
fetch_binance_klines_range(): [timestamp, open, high, low, close, volume],
timestamp = UTC-aware. Dadurch arbeiten alle bestehenden Module ohne Aenderung.

-----------------------------------------------------------------------------
WIE DUKASCOPY-DATEN FUNKTIONIEREN (fuer spaetere Wartung dokumentiert)
-----------------------------------------------------------------------------
1. Quelle:    Pro Stunde eine Datei mit ROH-TICKS (jede Kursaenderung), URL:
              https://datafeed.dukascopy.com/datafeed/{SYM}/{YYYY}/{MM}/{DD}/{HH}h_ticks.bi5
              ACHTUNG: MM ist 0-basiert (Januar=00, Dezember=11).
2. Format:    .bi5 = LZMA-komprimiert. Entpackt = Folge von 20-Byte-Records,
              BIG-ENDIAN: >IIIff
                uint32  ms-Offset ab Stundenbeginn
                uint32  Ask-Preis (ganzzahlig, in Punkten)
                uint32  Bid-Preis (ganzzahlig, in Punkten)
                float32 Ask-Volumen
                float32 Bid-Volumen
3. Punkt-Faktor: Roh-Preis / 10^digits = echter Preis. digits ist pro Wert
              unterschiedlich. Wir erkennen ihn automatisch ueber eine
              Plausibilitaets-Spanne (_PRICE_BAND), statt ihn fest zu kodieren
              — robust gegen falsche Annahmen.
4. Luecken:   Indizes laufen nicht 24/7. Fehlende Stunden (Nacht, Wochenende,
              Feiertag) -> 404 -> werden uebersprungen, NICHT kuenstlich
              gefuellt (sonst entstuenden Fake-Kerzen + falsche Signale).
5. Cache:     Verdichtete 1m-Kerzen werden lokal als CSV gespeichert
              (data/cache/dukascopy/{SYM}_1m.csv). Hoehere Timeframes werden
              aus dem 1m-Cache resampled -> Ticks werden nur EINMAL geladen.
-----------------------------------------------------------------------------
"""

import lzma
import struct
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
import requests

_BASE = "https://datafeed.dukascopy.com/datafeed"
_HEADERS = {"User-Agent": "Mozilla/5.0"}
_CACHE_DIR = Path("data/cache/dukascopy")

# Dukascopy-Instrument-Codes fuer unsere Nicht-Krypto-Werte.
# Schluessel sind die "menschlichen" Symbole, die im Backtest verwendet werden.
SYMBOL_MAP = {
    "XAU/USD": "XAUUSD",      # Gold
    "GOLD": "XAUUSD",
    "US30": "USA30IDXUSD",    # Dow Jones
    "DOW": "USA30IDXUSD",
    "NAS100": "USATECHIDXUSD",  # Nasdaq 100
    "NASDAQ": "USATECHIDXUSD",
    "US500": "USA500IDXUSD",  # S&P 500
    "SPX": "USA500IDXUSD",
    "SP500": "USA500IDXUSD",
    "GER40": "DEUIDXEUR",     # DAX
    "DAX": "DEUIDXEUR",
}

# Fester Punkt-Faktor je Roh-Code (empirisch verifiziert gegen Live-Chart:
# alle Dukascopy-Werte hier sind 3-stellig -> Roh-Integer / 1000 = echter Preis).
# Fest hinterlegt, damit die Skala NIE zwischen Laeufen springt.
_POINT_FACTOR = {
    "XAUUSD": 0.001,
    "USA30IDXUSD": 0.001,
    "USATECHIDXUSD": 0.001,
    "USA500IDXUSD": 0.001,
    "DEUIDXEUR": 0.001,
}

# Plausibilitaets-Spanne (weit) je Roh-Code -> nur Fallback-Erkennung fuer
# unbekannte Codes. Muss nur die richtige Zehnerpotenz grob treffen.
_PRICE_BAND = {
    "XAUUSD": (300, 20000),        # Gold USD/oz (mit Puffer nach oben)
    "USA30IDXUSD": (5000, 100000), # Dow
    "USATECHIDXUSD": (3000, 60000),# Nasdaq
    "USA500IDXUSD": (500, 20000),  # S&P
    "DEUIDXEUR": (3000, 60000),    # DAX
}

# pandas-Resample-Regeln je Backtest-Intervall
_RESAMPLE_RULE = {
    "1m": "1min", "5m": "5min", "15m": "15min",
    "30m": "30min", "1h": "1h", "4h": "4h",
}

_REC = struct.Struct(">IIIff")  # 20 Byte pro Tick


def _as_utc(dt) -> pd.Timestamp:
    """Macht aus beliebigem datetime-aehnlichem Wert einen UTC-aware Timestamp."""
    ts = pd.Timestamp(dt)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def _to_duka_symbol(symbol: str) -> str:
    """BTC/USDT -> nicht hier. XAU/USD -> XAUUSD, GER40 -> DEUIDXEUR."""
    key = symbol.upper().replace("-", "/")
    if key in SYMBOL_MAP:
        return SYMBOL_MAP[key]
    # Schon ein Dukascopy-Code?
    bare = key.replace("/", "")
    if bare in _PRICE_BAND:
        return bare
    raise ValueError(
        f"Unbekanntes Dukascopy-Symbol: {symbol!r}. "
        f"Bekannt: {sorted(SYMBOL_MAP)}"
    )


def _decode_bi5(raw: bytes, hour_start_ms: int, point: float) -> list:
    """Entpackt + dekodiert eine .bi5-Tickdatei zu [(ts_ms, bid, ask, vol), ...]."""
    if not raw:
        return []
    try:
        data = lzma.decompress(raw, format=lzma.FORMAT_AUTO)
    except lzma.LZMAError:
        data = lzma.decompress(raw, format=lzma.FORMAT_ALONE)
    out = []
    for off in range(0, len(data) - len(data) % _REC.size, _REC.size):
        ms, ask_i, bid_i, ask_v, bid_v = _REC.unpack_from(data, off)
        out.append((
            hour_start_ms + ms,
            bid_i * point,
            ask_i * point,
            ask_v + bid_v,
        ))
    return out


def _detect_point(sample_median_raw: float, code: str) -> float:
    """Findet den Punkt-Faktor (1/10^k) so, dass der MEDIAN-Preis in die Spanne faellt.

    Median statt Max -> robust gegen einzelne Ausreisser-Ticks.
    """
    lo, hi = _PRICE_BAND.get(code, (0.0001, 1e12))
    for k in range(0, 7):
        p = 10 ** (-k)
        val = sample_median_raw * p
        if lo <= val <= hi:
            return p
    # Fallback: 1/1000 (haeufigster Dukascopy-Wert)
    return 0.001


def _fetch_hour(code: str, ts_hour: pd.Timestamp) -> bytes:
    """Laedt eine stuendliche .bi5-Datei. Leerer bytes bei 404/keine Daten."""
    url = (
        f"{_BASE}/{code}/{ts_hour.year}/{ts_hour.month - 1:02d}/"
        f"{ts_hour.day:02d}/{ts_hour.hour:02d}h_ticks.bi5"
    )
    for attempt in range(3):
        try:
            r = requests.get(url, headers=_HEADERS, timeout=30)
            if r.status_code == 404:
                return b""
            r.raise_for_status()
            return r.content
        except Exception:
            if attempt == 2:
                return b""
            time.sleep(1.0 * (attempt + 1))
    return b""


def _build_1m_from_ticks(symbol: str, start_dt, end_dt, verbose: bool = False) -> pd.DataFrame:
    """Laedt alle Stunden-Tickdateien im Bereich und verdichtet zu 1m-Kerzen."""
    code = _to_duka_symbol(symbol)
    start = _as_utc(start_dt).floor("h")
    end = _as_utc(end_dt)
    hours = pd.date_range(start, end, freq="h", inclusive="left")

    if verbose:
        print(f"    [{symbol}->{code}] {len(hours)} Stunden-Dateien laden...", flush=True)

    raw_by_hour: dict = {}
    done = 0
    with ThreadPoolExecutor(max_workers=24) as ex:
        futs = {ex.submit(_fetch_hour, code, h): h for h in hours}
        for fut in as_completed(futs):
            h = futs[fut]
            raw_by_hour[h] = fut.result()
            done += 1
            if verbose and done % 250 == 0:
                pct = int(done / max(1, len(hours)) * 100)
                print(f"    [{code}] {pct}% ({done}/{len(hours)} Stunden)...", flush=True)

    # Punkt-Faktor: bevorzugt fest hinterlegt; sonst robuster Median-Fallback.
    point = _POINT_FACTOR.get(code)
    if point is not None:
        if verbose:
            print(f"    [{code}] Punkt-Faktor (fest): x{point}", flush=True)
    else:
        sample_raw: list = []
        for h in hours:
            raw = raw_by_hour.get(h)
            if not raw:
                continue
            ticks = _decode_bi5(raw, int(h.value // 1_000_000), 1.0)  # roh (point=1)
            sample_raw.extend(t[2] for t in ticks)  # rohe Ask-Werte
            if len(sample_raw) >= 5000:
                break
        if not sample_raw:
            return pd.DataFrame()
        median_raw = float(pd.Series(sample_raw).median())
        point = _detect_point(median_raw, code)
        if verbose:
            print(f"    [{code}] Punkt-Faktor erkannt (Median): x{point}", flush=True)

    # Alle Ticks dekodieren
    all_ticks = []
    for h in hours:
        raw = raw_by_hour.get(h)
        if raw:
            all_ticks.extend(_decode_bi5(raw, int(h.value // 1_000_000), point))

    if not all_ticks:
        return pd.DataFrame()

    tdf = pd.DataFrame(all_ticks, columns=["ts_ms", "bid", "ask", "vol"])
    tdf["timestamp"] = pd.to_datetime(tdf["ts_ms"], unit="ms", utc=True)
    tdf["mid"] = (tdf["bid"] + tdf["ask"]) / 2.0
    tdf = tdf.set_index("timestamp").sort_index()

    bars = tdf["mid"].resample("1min").ohlc()
    bars["volume"] = tdf["vol"].resample("1min").sum()
    bars = bars.dropna(subset=["open"]).reset_index()  # leere Minuten (Luecken) raus
    return bars[["timestamp", "open", "high", "low", "close", "volume"]]


def _cache_path(symbol: str) -> Path:
    code = _to_duka_symbol(symbol)
    return _CACHE_DIR / f"{code}_1m.csv"


def _load_or_build_1m(symbol: str, start_dt, end_dt, verbose: bool = False) -> pd.DataFrame:
    """Holt 1m-Kerzen aus dem Cache; laedt fehlenden Bereich nach."""
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _cache_path(symbol)
    start = _as_utc(start_dt)
    end = _as_utc(end_dt)

    cached = pd.DataFrame()
    if path.exists():
        cached = pd.read_csv(path, parse_dates=["timestamp"])
        if not cached.empty:
            cached["timestamp"] = pd.to_datetime(cached["timestamp"], utc=True)

    have_start = cached["timestamp"].min() if not cached.empty else None
    have_end = cached["timestamp"].max() if not cached.empty else None

    # Reicht der Cache? Toleranz von TOL fuer Markt-Pausen an den Raendern:
    # Indizes/Gold handeln nicht 24/7 -> die letzte/erste Kerze eines fixen
    # Fensters liegt oft Tage vor end_dt (Wochenende/Feiertag). Ohne Toleranz
    # wuerde der Check nie greifen und JEDER Aufruf neu herunterladen.
    TOL = pd.Timedelta(days=4)
    if (not cached.empty and have_start is not None and have_end is not None
            and have_start <= start + TOL and have_end >= end - TOL):
        sl = cached[(cached["timestamp"] >= start) & (cached["timestamp"] < end)]
        return sl.reset_index(drop=True)

    # Sonst: kompletten Bereich neu bauen und mit Cache verschmelzen
    fresh = _build_1m_from_ticks(symbol, start, end, verbose=verbose)
    if not cached.empty:
        merged = pd.concat([cached, fresh], ignore_index=True)
        merged = merged.drop_duplicates(subset=["timestamp"]).sort_values("timestamp")
    else:
        merged = fresh
    if not merged.empty:
        merged.to_csv(path, index=False)
    sl = merged[(merged["timestamp"] >= start) & (merged["timestamp"] < end)]
    return sl.reset_index(drop=True)


def fetch_dukascopy_klines_range(
    symbol: str,
    interval: str,
    start_dt,
    end_dt,
    verbose: bool = False,
) -> pd.DataFrame:
    """
    Laedt OHLCV-Kerzen von Dukascopy im angegebenen Zeitraum.

    symbol:   "XAU/USD"/"GOLD", "US30"/"DOW", "NAS100", "US500"/"SPX", "GER40"/"DAX"
    interval: "1m", "5m", "15m", "30m", "1h"
    start_dt / end_dt: pd.Timestamp (UTC) oder datetime-aehnlich

    Gibt DataFrame [timestamp, open, high, low, close, volume] zurueck
    (timestamp UTC-aware) — gleiches Format wie fetch_binance_klines_range().
    Handelspausen erzeugen Luecken (keine kuenstlichen Kerzen).
    """
    df_1m = _load_or_build_1m(symbol, start_dt, end_dt, verbose=verbose)
    if df_1m.empty or interval == "1m":
        return df_1m

    rule = _RESAMPLE_RULE.get(interval)
    if rule is None:
        raise ValueError(f"Nicht unterstuetztes Intervall: {interval!r}")

    idx = df_1m.set_index("timestamp")
    agg = idx.resample(rule).agg({
        "open": "first", "high": "max", "low": "min",
        "close": "last", "volume": "sum",
    })
    agg = agg.dropna(subset=["open"]).reset_index()
    return agg[["timestamp", "open", "high", "low", "close", "volume"]]
