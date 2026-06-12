"""Einmalskript: Marker-Koordinaten für ALLE Setups der letzten 500 Kerzen."""
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

df = fetch_binance_klines("BTCUSDT", "5m", 500)
df = calculate_wavetrend(df, n1=wt_cfg["n1"], n2=wt_cfg["n2"],
                         wt2_sma_length=wt_cfg["wt2_sma_length"])
df = detect_dots(df)
df = calculate_mfi(df, period=mfi_cfg["period"])
setups = detect_setups(df, config)

candles = df.set_index("timestamp")


def unix(ts):
    return int((ts - pd.Timestamp(0, tz="utc")) // pd.Timedelta(seconds=1))


markers = []
seen_anchors = set()
for _, s in setups.iterrows():
    is_long = s["direction"] == "long"
    a_key = (s["direction"], s["anchor_time"])
    if a_key not in seen_anchors:
        seen_anchors.add(a_key)
        c = candles.loc[s["anchor_time"]]
        markers.append({
            "label": "A",
            "time": unix(s["anchor_time"]),
            "utc": s["anchor_time"].strftime("%d.%m. %H:%M"),
            "price": round(c["low"] * 0.9985, 1) if is_long else round(c["high"] * 1.0015, 1),
            "direction": s["direction"],
            "mfi_ok": bool(s["mfi_filter_passed"]),  # für Anker: Optik nach erstem Trigger
            "wt1": float(s["anchor_wt1"]),
        })
    c = candles.loc[s["time"]]
    markers.append({
        "label": "T",
        "time": unix(s["time"]),
        "utc": s["time"].strftime("%d.%m. %H:%M"),
        "price": round(c["low"] * 0.9985, 1) if is_long else round(c["high"] * 1.0015, 1),
        "direction": s["direction"],
        "mfi_ok": bool(s["mfi_filter_passed"]),
        "wt1": float(s["trigger_wt1"]),
        "late": abs(float(s["trigger_wt1"])) < 30,  # näher an 0 als an ±60
    })

print(json.dumps(markers, indent=1))
