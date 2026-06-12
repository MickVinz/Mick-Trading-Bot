"""Sanity-Check: feuert detect_divergence irgendwo? Scan über ganze Serie."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

from scripts.verify_indicators import fetch_binance_klines, load_config
from src.indicators.wavetrend import calculate_wavetrend, detect_dots
from src.strategy.divergence_detector import detect_divergence

config = load_config()
wt = config["wavetrend"]
df = fetch_binance_klines("BTCUSDT", "5m", 1000)
df = calculate_wavetrend(df, n1=wt["n1"], n2=wt["n2"], wt2_sma_length=wt["wt2_sma_length"])
df = detect_dots(df)

hits = {"long": 0, "short": 0}
examples = []
for i in range(30, len(df)):
    for direction in ("long", "short"):
        d = detect_divergence(df, i, direction, config)
        if d["divergence_active"]:
            hits[direction] += 1
            if len(examples) < 6:
                examples.append((df["timestamp"].iloc[i].strftime("%d.%m %H:%M"),
                                 direction, d["type"],
                                 d["point_old"], d["point_new"]))

print(f"Divergenz-Treffer über {len(df)} Kerzen: {hits}")
for ts, dirn, typ, old, new in examples:
    print(f"  {ts} {dirn}/{typ}: alt P{old['price']}/wt{old['wt1']} "
          f"-> neu P{new['price']}/wt{new['wt1']}")
