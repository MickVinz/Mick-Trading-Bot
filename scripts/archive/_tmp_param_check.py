"""Welche WaveTrend-Parameter passen zu Market Cipher B? Schneller Live-Vergleich."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

from scripts.verify_indicators import fetch_binance_klines
from src.indicators.wavetrend import calculate_wavetrend

df = fetch_binance_klines("BTCUSDT", "5m", 500)

variants = [
    ("LazyBear 10/21/4 (Spec)", dict(n1=10, n2=21, wt2_sma_length=4)),
    ("VuManChu   9/12/3", dict(n1=9, n2=12, wt2_sma_length=3)),
]

last_ts = df["timestamp"].iloc[-1].strftime("%H:%M")
print(f"Live-Kerze {last_ts} UTC | close {df['close'].iloc[-1]:.2f}")
for name, params in variants:
    r = calculate_wavetrend(df, **params).iloc[-1]
    print(f"  {name}: wt1 {r['wt1']:7.2f} | wt2 {r['wt2']:7.2f}")
