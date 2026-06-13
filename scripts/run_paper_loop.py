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

import datetime
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

from src.paper.journal import Journal
from src.paper.paper_engine import run_once

CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"

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


def main() -> None:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    journal = Journal(config)
    balance = journal.state["balance"]
    pos = journal.state.get("open_position")

    print("=" * 60)
    print("  Mick Trading Bot — Paper-Trading-Loop")
    print("  KEINE ECHTEN ORDERS. Reine Simulation.")
    print("=" * 60)
    print(f"  Balance:          {balance:.2f} USDT")
    if pos:
        print(f"  Offene Position:  {pos.direction.upper()} "
              f"@{pos.entry:.2f} | SL {pos.sl:.2f} | TP1 {pos.tp1:.2f}")
    else:
        print("  Offene Position:  keine")
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
                result = run_once(journal, config)
            except Exception as exc:
                print(f"  ❌ Fehler im Durchlauf: {exc}", flush=True)
                continue

            # --- Ausgabe ---
            if result["exit_reason"]:
                print(
                    f"  EXIT  : {result['exit_reason'].upper()} "
                    f"@{result['exit_price']:.2f}",
                    flush=True,
                )

            print(
                f"  Setup : {'JA ✓' if result['setup_found'] else 'nein'}",
                flush=True,
            )

            if result["entry_taken"]:
                p = journal.state["open_position"]
                print(
                    f"  ENTRY : {p.direction.upper()} @{p.entry:.2f} "
                    f"| SL {p.sl:.2f} | TP1 {p.tp1:.2f} | qty {p.qty:.6f}",
                    flush=True,
                )

            pos = journal.state.get("open_position")
            pos_str = (
                f"{pos.direction.upper()} @{pos.entry:.2f}"
                if pos else "keine"
            )
            print(f"  Pos   : {pos_str}", flush=True)
            print(f"  Bal   : {result['balance']:.2f} USDT", flush=True)

    except KeyboardInterrupt:
        print("\n\nLoop beendet (Ctrl+C). State gespeichert.")
        pos = journal.state.get("open_position")
        if pos:
            print(
                f"ACHTUNG: Offene Position {pos.direction.upper()} "
                f"@{pos.entry:.2f} läuft noch. "
                "state.json enthält sie — wird beim nächsten Start fortgeführt."
            )


if __name__ == "__main__":
    main()
