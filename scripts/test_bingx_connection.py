"""
Verbindungstest BingX (Paper Trading, NUR lesend — keine Orders).

Was das Skript macht:
    1. Verbindet mit BingX (öffentliche Endpunkte, KEIN API-Key nötig).
    2. Lädt 500 BTC/USDT 5m-Kerzen und zeigt die letzten 5 als Tabelle.
    3. Spread-Check: vergleicht den aktuellen Preis BingX vs. Binance.
    4. Führt die komplette Analyse-Pipeline aus
       (WaveTrend -> MFI -> Setup-Erkennung -> Trade-Levels)
       und zeigt, ob aktuell ein gültiges Setup vorliegt.

Aufruf (aus dem Projektordner):
    python scripts/test_bingx_connection.py
"""

import sys
from pathlib import Path

# Windows-Konsole auf UTF-8 (Umlaute / "grün" korrekt darstellen).
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

import pandas as pd
import requests
import yaml

# Projekt-Root importierbar machen, egal von wo gestartet wird.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.exchange.bingx_client import fetch_bingx_klines, fetch_bingx_price
from src.indicators.wavetrend import calculate_wavetrend, detect_dots
from src.indicators.mfi import calculate_mfi
from src.strategy.setup_detector import detect_setups
from src.strategy.trade_levels import calculate_trade_levels

CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"
BINANCE_PRICE_URL = "https://api.binance.com/api/v3/ticker/price"


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def fetch_binance_price(symbol: str = "BTCUSDT") -> float:
    """Aktueller Binance-Spot-Preis (öffentlich, kein Key)."""
    resp = requests.get(BINANCE_PRICE_URL, params={"symbol": symbol}, timeout=15)
    resp.raise_for_status()
    return float(resp.json()["price"])


def main() -> None:
    config = load_config()

    # BingX nutzt "BTC-USDT" (Bindestrich), Binance "BTCUSDT" (siehe Client-Doku).
    bingx_symbol = config["market"]["symbol"].replace("/", "-")    # "BTC/USDT" -> "BTC-USDT"
    binance_symbol = config["market"]["symbol"].replace("/", "")   # "BTC/USDT" -> "BTCUSDT"
    interval = config["market"]["timeframe"]

    # ------------------------------------------------------------------
    # 1. Kerzen von BingX laden
    # ------------------------------------------------------------------
    print(f"Lade 500 x {interval}-Kerzen für {bingx_symbol} von BingX ...")
    try:
        df = fetch_bingx_klines(bingx_symbol, interval, limit=500)
    except (requests.RequestException, RuntimeError) as exc:
        sys.exit(f"FEHLER beim Laden von BingX: {exc}")

    print(f"OK — {len(df)} geschlossene Kerzen geladen "
          f"(letzte: {df['timestamp'].iloc[-1]:%Y-%m-%d %H:%M} UTC).")

    # ------------------------------------------------------------------
    # 2. Letzte 5 Kerzen als Tabelle
    # ------------------------------------------------------------------
    table = df[["timestamp", "open", "high", "low", "close", "volume"]].tail(5).copy()
    table["timestamp"] = table["timestamp"].dt.strftime("%Y-%m-%d %H:%M")
    for col in ("open", "high", "low", "close"):
        table[col] = table[col].map("{:.1f}".format)
    table["volume"] = table["volume"].map("{:.2f}".format)
    print("\nLetzte 5 Kerzen (UTC):")
    print(table.to_string(index=False))

    # ------------------------------------------------------------------
    # 3. Spread-Check: BingX vs. Binance
    # ------------------------------------------------------------------
    print("\nSpread-Check (aktueller Preis):")
    try:
        bingx_price = fetch_bingx_price(bingx_symbol)
        binance_price = fetch_binance_price(binance_symbol)
        diff_abs = bingx_price - binance_price
        diff_pct = diff_abs / binance_price * 100
        print(f"  BingX (Perp):   {bingx_price:,.2f} USDT")
        print(f"  Binance (Spot): {binance_price:,.2f} USDT")
        print(f"  Abweichung:     {diff_abs:+.2f} USDT ({diff_pct:+.4f} %)")
        print("  Hinweis: BingX = Perpetual-Futures, Binance = Spot. "
              "Eine kleine Abweichung (Funding/Basis) ist normal.")
    except (requests.RequestException, RuntimeError, KeyError) as exc:
        print(f"  Spread-Check übersprungen (Fehler: {exc})")

    # ------------------------------------------------------------------
    # 4. Komplette Pipeline auf den BingX-Kerzen
    # ------------------------------------------------------------------
    wt_cfg = config["wavetrend"]
    df = calculate_wavetrend(df, n1=wt_cfg["n1"], n2=wt_cfg["n2"],
                             wt2_sma_length=wt_cfg["wt2_sma_length"])
    df = detect_dots(df)
    df = calculate_mfi(df, period=config["mfi"]["period"])

    setups = detect_setups(df, config)
    setups = calculate_trade_levels(df, setups, config)

    print("\nPipeline-Ergebnis (WaveTrend -> MFI -> Setup -> Trade-Levels):")
    if setups.empty:
        print("  Keine Anker->Trigger-Setups im geladenen Zeitraum gefunden.")
        return

    valid = setups[setups["setup_valid"]]
    print(f"  {len(setups)} Setup(s) erkannt, davon {len(valid)} gültig.")

    # Jüngstes gültiges Setup zeigen — und ob es die LETZTE Kerze betrifft
    # (= "liegt aktuell ein Setup vor?").
    cols = ["time", "direction", "anchor_wt1", "trigger_wt1", "trigger_mfi",
            "mfi_filter_passed", "divergence_active", "setup_valid",
            "entry", "sl", "tp1", "rr_ratio", "sl_risiko_pct"]
    cols = [c for c in cols if c in setups.columns]

    last_setup = setups.iloc[-1]
    last_candle_time = df["timestamp"].iloc[-1]
    is_current = last_setup["time"] == last_candle_time

    print("\n  Jüngstes Setup:")
    show = setups[cols].tail(1).copy()
    show["time"] = pd.to_datetime(show["time"]).dt.strftime("%Y-%m-%d %H:%M")
    print(show.to_string(index=False))

    if is_current and last_setup["setup_valid"]:
        print("\n  >>> AKTUELL liegt ein GÜLTIGES Setup auf der letzten "
              "geschlossenen Kerze vor.")
    elif is_current:
        print("\n  >>> Auf der letzten Kerze entstand ein Setup, ist aber "
              "NICHT gültig (Filter/Warmup/SL).")
    else:
        mins = (last_candle_time - last_setup["time"]).total_seconds() / 60
        print(f"\n  >>> Kein Setup auf der aktuellen Kerze "
              f"(jüngstes liegt {mins:.0f} Min zurück).")


if __name__ == "__main__":
    main()
