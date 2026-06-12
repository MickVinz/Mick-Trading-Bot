"""Einmaliges Vergleichsskript: Binance-API vs. TradingView-MCP-Daten."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

import pandas as pd
from scripts.verify_indicators import fetch_binance_klines
from src.indicators.wavetrend import calculate_wavetrend, detect_dots
from src.indicators.mfi import calculate_mfi

# OHLC-Bars aus TradingView (per MCP gelesen, BINANCE:BTCUSDT 5m, UTC-Unix-Sekunden)
TV_BARS = [
    {"time": 1781246100, "open": 62936.01, "high": 62974.00, "low": 62829.81, "close": 62961.99, "volume": 79.00468},
    {"time": 1781246400, "open": 62961.99, "high": 62973.99, "low": 62911.83, "close": 62924.08, "volume": 121.14279},
    {"time": 1781246700, "open": 62924.08, "high": 63014.34, "low": 62914.60, "close": 62979.20, "volume": 129.67122},
    {"time": 1781247000, "open": 62979.19, "high": 62998.00, "low": 62919.99, "close": 62968.57, "volume": 155.82169},
    {"time": 1781247300, "open": 62968.57, "high": 63020.00, "low": 62934.36, "close": 62980.45, "volume": 69.35727},
]

df = fetch_binance_klines("BTCUSDT", "5m", 500)
# Auflösungsunabhängige Umrechnung in Unix-Sekunden (pandas kann ms- oder ns-Auflösung halten)
df["unix"] = (df["timestamp"] - pd.Timestamp(0, tz="utc")) // pd.Timedelta(seconds=1)

print("=== OHLC-Vergleich: Binance-API vs. TradingView-Chart ===")
max_dev = 0.0
for tv in TV_BARS:
    row = df[df["unix"] == tv["time"]]
    if row.empty:
        print(f"  {tv['time']}: nicht in Binance-Daten gefunden")
        continue
    r = row.iloc[0]
    ts = r["timestamp"].strftime("%H:%M")
    devs = []
    for f in ("open", "high", "low", "close"):
        dev = abs(r[f] - tv[f]) / tv[f] * 100
        devs.append(dev)
        max_dev = max(max_dev, dev)
    print(f"  {ts} UTC | O {r['open']:.2f}/{tv['open']:.2f} | H {r['high']:.2f}/{tv['high']:.2f} "
          f"| L {r['low']:.2f}/{tv['low']:.2f} | C {r['close']:.2f}/{tv['close']:.2f} "
          f"| max Abw. {max(devs):.5f} %  (Binance/TV)")
print(f"  -> Maximale OHLC-Abweichung über alle Kerzen: {max_dev:.5f} %")

# Indikatoren inkl. der noch laufenden Kerze berechnen (TV-Datenfenster zeigt Live-Kerze)
df = calculate_wavetrend(df)
df = detect_dots(df)
df = calculate_mfi(df)

print()
print("=== Meine berechneten Werte (letzte 3 Kerzen, inkl. laufender) ===")
for _, r in df.tail(3).iterrows():
    print(f"  {r['timestamp'].strftime('%H:%M')} UTC | close {r['close']:.2f} "
          f"| wt1 {r['wt1']:.2f} | wt2 {r['wt2']:.2f} | dot {r['dot']} | mfi {r['mfi']:.2f}")

print()
print("=== Meine letzten 5 Dot-Kreuzungen (zum Abgleich mit MCB-Wellen) ===")
for _, r in df[df["dot"] != "-"].tail(5).iterrows():
    print(f"  {r['timestamp'].strftime('%H:%M')} UTC | {r['dot']} | wt1 {r['wt1']:.2f} | wt2 {r['wt2']:.2f}")
