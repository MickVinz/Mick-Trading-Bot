"""
Paper-Trading-Loop — taktet an echten 5m-Kerzenschlüssen.

Ablauf:
    1. Berechnet Sekunden bis zur nächsten 5m-UTC-Grenze + 5s Puffer.
    2. Schläft, lädt dann frische BingX-Kerzen, läuft Pipeline durch.
    3. Loggt pro Durchlauf: Zeit, Setup?, Position, Balance.
    4. Schreibt state.json nach jedem Durchlauf → Neustart ohne Datenverlust.

Beenden: Ctrl+C (sauber, laufende Position bleibt in state.json erhalten).

Aufruf (aus dem Projektordner):
    python scripts/run_paper_loop.py
"""

import csv
import datetime
import json
import math
import sys
import time
from pathlib import Path

try:
    from zoneinfo import ZoneInfo as _ZoneInfo
    _TZ = _ZoneInfo("Europe/Berlin")
except Exception:
    _TZ = None

def _now_berlin() -> datetime.datetime:
    if _TZ:
        return datetime.datetime.now(_TZ)
    return datetime.datetime.now().astimezone()

# Projekt-Root importierbar machen
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import yaml

from src.config_utils import get_symbols
from src.paper.journal import Journal
from src.paper.paper_engine import run_cycle
from src.paper.stats import compute_stats

CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"
TRADES_PATH = PROJECT_ROOT / "data" / "trades.csv"
STATS_PATH = PROJECT_ROOT / "data" / "stats.json"

# Intervall in Sekunden (5m)
_INTERVAL_S = 300
# Puffer nach Kerzenschluss: gibt BingX Zeit, die geschlossene Kerze
# in der API bereitzustellen, bevor wir abrufen.
_BUFFER_S = 5


def _secs_to_next_boundary(buffer_s: int = _BUFFER_S) -> float:
    """
    Sekunden bis zur nächsten 5m-UTC-Grenze + Puffer.

    Beispiel: Jetzt 14:23:12 → nächste Grenze 14:25:00 + 5s = 14:25:05.
    Mindest-Wartezeit = buffer_s (verhindert 0s-Wait exakt auf der Grenze).
    """
    now = time.time()
    next_boundary = math.ceil(now / _INTERVAL_S) * _INTERVAL_S
    wait = next_boundary - now + buffer_s
    # Sicherheit: nie weniger als buffer_s warten
    return max(wait, buffer_s)


def _write_stats(config: dict) -> None:
    """Liest trades.csv, berechnet Kennzahlen, schreibt data/stats.json (fuers Dashboard)."""
    start_balance = float(config.get("paper_trading", {}).get("start_balance", 1000.0))
    trades = []
    if TRADES_PATH.exists():
        with TRADES_PATH.open(encoding="utf-8") as f:
            trades = list(csv.DictReader(f))
    stats = compute_stats(trades, start_balance=start_balance)
    stats["generated_at"] = _now_berlin().strftime("%Y-%m-%d %H:%M:%S")
    with STATS_PATH.open("w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2)


def main() -> None:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    journal = Journal(config)
    symbols = get_symbols(config)
    balance = journal.state["balance"]

    print("=" * 60)
    print("  Mick Trading Bot — Paper-Trading-Loop (Multi-Coin)")
    print("  KEINE ECHTEN ORDERS. Reine Simulation.")
    print("=" * 60)
    print(f"  Balance (geteilt): {balance:.2f} USDT")
    print(f"  Coins:             {', '.join(symbols)}")
    open_now = [s for s in symbols if journal.get_position(s) is not None]
    print(f"  Offene Positionen: {len(open_now)} ({', '.join(open_now) or 'keine'})")
    print("  Beenden: Ctrl+C")
    print("=" * 60)

    try:
        while True:
            wait = _secs_to_next_boundary()
            next_run_dt = _now_berlin() + datetime.timedelta(seconds=wait)
            print(
                f"\nNächster Durchlauf: {next_run_dt:%Y-%m-%d %H:%M:%S} "
                f"(in {wait:.0f}s) ...",
                flush=True,
            )
            time.sleep(wait)

            now_str = _now_berlin().strftime("%Y-%m-%d %H:%M:%S")
            print(f"\n[{now_str}]", flush=True)

            try:
                results = run_cycle(journal, config)
            except Exception as exc:
                print(f"  ❌ Fehler im Zyklus: {exc}", flush=True)
                continue

            # --- Ausgabe: eine Zeile pro Coin ---
            for symbol in get_symbols(config):
                r = results.get(symbol)
                if r is None:
                    print(f"  {symbol}: (uebersprungen)", flush=True)
                    continue
                line = f"  {symbol}: "
                if r["exit_reason"]:
                    line += f"EXIT {r['exit_reason'].upper()} @{r['exit_price']:.4f} | "
                if r["entry_taken"]:
                    p = journal.get_position(symbol)
                    line += f"ENTRY {p.direction.upper()} @{p.entry:.4f} | "
                if r.get("book_cap_hit"):
                    line += "Setup (Buch-Cap, kein Entry) | "
                pos = journal.get_position(symbol)
                pos_str = (f"{pos.direction.upper()}@{pos.entry:.4f}"
                           if pos else "flach")
                line += f"Pos: {pos_str}"
                print(line, flush=True)

            print(f"  Balance: {journal.state['balance']:.2f} USDT", flush=True)

            # Kennzahlen fuers Dashboard aktualisieren (best effort).
            try:
                _write_stats(config)
            except Exception as exc:
                print(f"  ⚠ stats.json nicht geschrieben: {exc}", flush=True)

    except KeyboardInterrupt:
        print("\n\nLoop beendet (Ctrl+C). State gespeichert.")
        open_now = [s for s in get_symbols(config)
                    if journal.get_position(s) is not None]
        if open_now:
            print(
                f"ACHTUNG: {len(open_now)} offene Position(en) ({', '.join(open_now)}) "
                "laufen noch. state.json enthält sie — wird beim nächsten Start fortgeführt."
            )


if __name__ == "__main__":
    main()
