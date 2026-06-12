"""
Testskript: Anker→Trigger-Setup-Erkennung auf den letzten 500 5m-Kerzen.

Lädt BTC/USDT-Kerzen von der öffentlichen Binance-API, berechnet WaveTrend
und MFI (Parameter aus config/config.yaml) und gibt alle erkannten Setups
als Tabelle aus — mit UTC- UND lokaler Zeit, damit du die Signale direkt
auf deinem TradingView-Chart wiederfindest.

Aufruf:
    python scripts/test_setups.py
    python scripts/test_setups.py --limit 500 --csv meine_kerzen.csv
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

from scripts.verify_indicators import fetch_binance_klines, load_csv, load_config
from src.indicators.wavetrend import calculate_wavetrend, detect_dots
from src.indicators.mfi import calculate_mfi
from src.strategy.setup_detector import detect_setups


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Anker→Trigger-Setups auf historischen Kerzen erkennen."
    )
    parser.add_argument("--csv", help="CSV-Datei statt Binance-API nutzen")
    parser.add_argument("--limit", type=int, default=500,
                        help="Anzahl zu ladender Kerzen (Default: 500)")
    args = parser.parse_args()

    config = load_config()
    wt_cfg = config["wavetrend"]
    mfi_cfg = config["mfi"]

    # ------------------------------------------------------------------
    # 1. Kerzen laden
    # ------------------------------------------------------------------
    if args.csv:
        print(f"Lade Kerzen aus CSV: {args.csv}")
        df = load_csv(args.csv)
    else:
        symbol = config["market"]["symbol"].replace("/", "")
        interval = config["market"]["timeframe"]
        print(f"Lade {args.limit} x {interval}-Kerzen für {symbol} von Binance ...")
        df = fetch_binance_klines(symbol, interval, args.limit)

    # ------------------------------------------------------------------
    # 2. Indikatoren berechnen (alles aus der Config, nichts hartkodiert)
    # ------------------------------------------------------------------
    df = calculate_wavetrend(
        df,
        n1=wt_cfg["n1"],
        n2=wt_cfg["n2"],
        wt2_sma_length=wt_cfg["wt2_sma_length"],
    )
    df = detect_dots(df)
    df = calculate_mfi(df, period=mfi_cfg["period"])

    # ------------------------------------------------------------------
    # 3. Setups erkennen
    # ------------------------------------------------------------------
    setups = detect_setups(df, config)

    span_start = df["timestamp"].iloc[0].strftime("%Y-%m-%d %H:%M")
    span_end = df["timestamp"].iloc[-1].strftime("%Y-%m-%d %H:%M")
    print(f"\nZeitraum: {span_start} bis {span_end} UTC "
          f"({len(df)} Kerzen, Anker-Schwelle ±{config['anchor_trigger']['anchor_threshold']})")

    if setups.empty:
        print("Keine vollständigen Anker→Trigger-Setups im Zeitraum gefunden.")
        return

    # ------------------------------------------------------------------
    # 4. Tabelle aufbereiten: UTC + lokale Zeit nebeneinander
    # ------------------------------------------------------------------
    local_tz = datetime.now().astimezone().tzinfo  # Zeitzone deines Rechners

    out = setups.copy()
    out.insert(0, "zeit_lokal",
               out["time"].dt.tz_convert(local_tz).dt.strftime("%d.%m. %H:%M"))
    out.insert(1, "zeit_utc", out["time"].dt.strftime("%d.%m. %H:%M"))
    out["anchor_time"] = out["anchor_time"].dt.strftime("%H:%M")
    out = out.drop(columns=["time"])

    # Spalten lesbar umbenennen
    out = out.rename(columns={
        "direction": "richtung",
        "anchor_time": "anker_utc",
        "anchor_wt1": "anker_wt1",
        "anchor_mfi": "anker_mfi",
        "trigger_wt1": "trig_wt1",
        "trigger_mfi": "trig_mfi",
        "mfi_filter_passed": "mfi_ok",
        "divergence_active": "div",
        "setup_valid": "valid",
    })

    print(f"\n{len(out)} Setup(s) erkannt "
          f"(lokale Zeitzone: {local_tz}):\n")
    print(out.to_string(index=False))

    n_valid = int(setups["setup_valid"].sum())
    n_div = int(setups["divergence_active"].sum())
    print(f"\nDavon gültig: {n_valid} von {len(setups)} "
          f"| mit aktiver Divergenz: {n_div}")
    print("Hinweis: aktive Divergenz ('div'=True) hebt den MFI-Filter auf.")


if __name__ == "__main__":
    main()
