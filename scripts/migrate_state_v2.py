"""
Einmalige Migration der Paper-Trading-Daten auf das Multi-Coin-Schema (v2).

- state.json: v1 (single open_position) -> v2 (positions-Dict). Backup: state.json.bak
- trades.csv: ergaenzt die fehlende erste Spalte 'symbol' mit 'BTC/USDT'.
              Backup: trades.csv.bak

Idempotent: bereits migrierte Dateien werden erkannt und nicht doppelt angefasst.
Aufruf: python scripts/migrate_state_v2.py
"""
import csv
import json
import shutil
import sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"

_NEW_HEADER = [
    "symbol", "entry_time", "exit_time", "direction", "entry", "sl", "tp1",
    "exit_price", "exit_reason", "qty", "risk_pct", "rr", "pnl_usd", "pnl_pct",
    "balance_after", "divergence",
]


def migrate_state(path: Path) -> None:
    if not path.exists():
        print(f"  state.json nicht vorhanden ({path}) — uebersprungen.")
        return
    raw = json.loads(path.read_text(encoding="utf-8"))
    if "positions" in raw:
        print("  state.json ist bereits v2 — uebersprungen.")
        return
    shutil.copy2(path, path.with_suffix(".json.bak"))
    old_pos = raw.get("open_position")
    old_lct = raw.get("last_candle_time")
    new = {
        "version": 2,
        "balance": raw.get("balance", 1000.0),
        "positions": {"BTC/USDT": old_pos} if old_pos else {},
        "day_start_balance": raw.get("day_start_balance", raw.get("balance", 1000.0)),
        "day": raw.get("day", ""),
        "realized_pnl_today": raw.get("realized_pnl_today", 0.0),
        "last_candle_time": {"BTC/USDT": old_lct} if old_lct else {},
    }
    path.write_text(json.dumps(new, indent=2), encoding="utf-8")
    print("  state.json -> v2 migriert (Backup: state.json.bak).")


def migrate_trades(path: Path) -> None:
    if not path.exists():
        print(f"  trades.csv nicht vorhanden ({path}) — uebersprungen.")
        return
    rows = list(csv.reader(path.open(encoding="utf-8")))
    if not rows:
        print("  trades.csv leer — uebersprungen.")
        return
    if rows[0] and rows[0][0] == "symbol":
        print("  trades.csv hat bereits symbol-Spalte — uebersprungen.")
        return
    shutil.copy2(path, path.with_suffix(".csv.bak"))
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(_NEW_HEADER)
        for row in rows[1:]:   # alte Headerzeile verwerfen, Daten mit BTC/USDT praefixen
            w.writerow(["BTC/USDT", *row])
    print("  trades.csv -> symbol-Spalte ergaenzt (Backup: trades.csv.bak).")


def main() -> None:
    print("Migration auf Multi-Coin-Schema (v2)\n")
    migrate_state(DATA_DIR / "state.json")
    migrate_trades(DATA_DIR / "trades.csv")
    print("\nFertig.")


if __name__ == "__main__":
    main()
