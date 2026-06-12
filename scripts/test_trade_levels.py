"""
Testskript: Trade-Levels (Entry/SL/TP1) für alle erkannten Setups.

Lädt 500 geschlossene 5m-Kerzen (Repaint-Schutz aktiv), erkennt die
Anker→Trigger-Setups und berechnet für jedes die konkreten Trade-Levels
nach Spec. Ausgabe als Tabelle:

    Zeit | Richtung | Entry | SL | TP1 | SL-Risiko % | RR

Aufruf:
    python scripts/test_trade_levels.py
    python scripts/test_trade_levels.py --json   (Maschinen-Format, z. B.
                                                  für Chart-Markierungen)
"""

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

from scripts.verify_indicators import fetch_binance_klines, load_config
from src.indicators.wavetrend import calculate_wavetrend, detect_dots
from src.indicators.mfi import calculate_mfi
from src.strategy.setup_detector import detect_setups
from src.strategy.trade_levels import calculate_trade_levels


def main() -> None:
    parser = argparse.ArgumentParser(description="Trade-Levels pro Setup berechnen.")
    parser.add_argument("--limit", type=int, default=500,
                        help="Anzahl zu ladender Kerzen (Default: 500)")
    parser.add_argument("--json", action="store_true",
                        help="Ausgabe als JSON (inkl. Unix-Zeit) statt Tabelle")
    args = parser.parse_args()

    config = load_config()
    wt_cfg, mfi_cfg = config["wavetrend"], config["mfi"]

    # 1. Geschlossene Kerzen laden (Repaint-Schutz in fetch_binance_klines)
    symbol = config["market"]["symbol"].replace("/", "")
    df = fetch_binance_klines(symbol, config["market"]["timeframe"], args.limit)

    # 2. Indikatoren + Setup-Erkennung (Parameter aus config.yaml)
    df = calculate_wavetrend(df, n1=wt_cfg["n1"], n2=wt_cfg["n2"],
                             wt2_sma_length=wt_cfg["wt2_sma_length"])
    df = detect_dots(df)
    df = calculate_mfi(df, period=mfi_cfg["period"])
    setups = detect_setups(df, config)

    if setups.empty:
        print("Keine Setups im Zeitraum gefunden.")
        return

    # 3. Trade-Levels berechnen
    levels = calculate_trade_levels(df, setups, config)

    if args.json:
        out = levels.copy()
        out["unix"] = (out["time"] - pd.Timestamp(0, tz="utc")) // pd.Timedelta(seconds=1)
        out["time"] = out["time"].dt.strftime("%Y-%m-%d %H:%M")
        out["anchor_time"] = out["anchor_time"].dt.strftime("%Y-%m-%d %H:%M")
        print(json.dumps(out.to_dict(orient="records"), indent=1, default=str))
        return

    # 4. Tabelle: Zeit | Richtung | Entry | SL | TP1 | SL-Risiko % | RR
    table = levels.copy()
    table["zeit_utc"] = table["time"].dt.strftime("%d.%m. %H:%M")
    # Flags robust ergänzen (falls Spalte bei leerem Sonderfall fehlt)
    for col in ("sl_zu_eng", "warmup_artefact"):
        if col not in table.columns:
            table[col] = False

    table = table[["zeit_utc", "direction", "entry", "sl", "tp1",
                   "sl_risiko_pct", "rr_ratio", "warmup_artefact", "sl_zu_eng",
                   "sl_quelle", "setup_valid"]]
    table = table.rename(columns={
        "direction": "richtung", "sl_risiko_pct": "sl_risiko_%",
        "rr_ratio": "rr", "warmup_artefact": "warmup", "sl_zu_eng": "sl_eng",
        "sl_quelle": "sl_quelle", "setup_valid": "mfi_valid",
    })

    span = (f"{levels['time'].iloc[0]:%Y-%m-%d %H:%M} bis "
            f"{levels['time'].iloc[-1]:%Y-%m-%d %H:%M} UTC")
    print(f"\n{len(table)} Setup(s) mit Trade-Levels ({span}):\n")
    print(table.to_string(index=False))
    print("\nExit-Regel: volle Position bei TP1 (RR 2:1). Kein Teilausstieg, "
          "kein Rest-Exit (Produktentscheidung 2026-06-12).")


if __name__ == "__main__":
    main()
