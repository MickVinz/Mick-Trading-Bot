"""Einmalskript: Setup-Marker-Koordinaten für TradingView-Zeichnung erzeugen."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

import pandas as pd
from scripts.verify_indicators import fetch_binance_klines, load_config
from src.indicators.wavetrend import calculate_wavetrend, detect_dots
from src.indicators.mfi import calculate_mfi
from src.strategy.setup_detector import detect_setups

config = load_config()
wt_cfg, mfi_cfg = config["wavetrend"], config["mfi"]

# 500 Kerzen laden (Indikator-Aufwärmphase), Setups erkennen,
# dann auf die letzten 150 Kerzen filtern.
df = fetch_binance_klines("BTCUSDT", "5m", 500)
df = calculate_wavetrend(df, n1=wt_cfg["n1"], n2=wt_cfg["n2"],
                         wt2_sma_length=wt_cfg["wt2_sma_length"])
df = detect_dots(df)
df = calculate_mfi(df, period=mfi_cfg["period"])
setups = detect_setups(df, config)

window_start = df["timestamp"].iloc[-150]
in_window = setups[setups["time"] >= window_start]

# Kerzen-Lookup für Preis-Koordinaten (Marker unter/über die Kerze setzen)
candles = df.set_index("timestamp")


def unix(ts):
    return int((ts - pd.Timestamp(0, tz="utc")) // pd.Timedelta(seconds=1))


markers = []
seen_anchors = set()
for _, s in in_window.iterrows():
    is_long = s["direction"] == "long"
    # Anker-Marker (pro Anker nur einmal, mehrere Trigger teilen sich einen)
    a_key = (s["direction"], s["anchor_time"])
    if a_key not in seen_anchors:
        seen_anchors.add(a_key)
        c = candles.loc[s["anchor_time"]]
        markers.append({
            "label": "A",
            "time": unix(s["anchor_time"]),
            "price": round(c["low"] * 0.9985, 1) if is_long else round(c["high"] * 1.0015, 1),
            "direction": s["direction"],
            "mfi_ok": bool(s["mfi_filter_passed"]),
            "wt1": s["anchor_wt1"],
        })
    c = candles.loc[s["time"]]
    markers.append({
        "label": "T",
        "time": unix(s["time"]),
        "price": round(c["low"] * 0.9985, 1) if is_long else round(c["high"] * 1.0015, 1),
        "direction": s["direction"],
        "mfi_ok": bool(s["mfi_filter_passed"]),
        "wt1": s["trigger_wt1"],
    })

print(f"Fenster ab {window_start:%Y-%m-%d %H:%M} UTC | Setups im Fenster: {len(in_window)}")
print(json.dumps(markers, indent=1))
