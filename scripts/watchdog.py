"""
watchdog.py — startet run_paper_loop.py und startet bei Absturz automatisch neu.
Schreibt data/watchdog_status.json fuer das Dashboard.

Starten: python scripts/watchdog.py
Stoppen: Strg+C
"""
import subprocess
import sys
import time
import json
import threading
from pathlib import Path
from datetime import datetime, timezone

ROOT        = Path(__file__).parent.parent
STATUS_FILE = ROOT / "data" / "watchdog_status.json"
BOT_SCRIPT  = ROOT / "scripts" / "run_paper_loop.py"

RESTART_DELAY      = 10   # Sekunden warten vor Neustart
HEARTBEAT_INTERVAL = 30   # Sekunden zwischen Heartbeat-Updates


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def write_status(status, restart_count, last_crash=None, bot_pid=None, last_error=None):
    STATUS_FILE.parent.mkdir(exist_ok=True)
    STATUS_FILE.write_text(json.dumps({
        "watchdog_alive": now_iso(),
        "bot_status":    status,        # "running" | "restarting" | "stopped"
        "restart_count": restart_count,
        "last_crash":    last_crash,
        "last_error":    last_error,
        "bot_pid":       bot_pid,
    }, indent=2, ensure_ascii=False), encoding="utf-8")


def _heartbeat_thread(state: dict, stop_event: threading.Event):
    while not stop_event.is_set():
        write_status(
            state["status"],
            state["restart_count"],
            state["last_crash"],
            bot_pid=state["bot_pid"],
        )
        stop_event.wait(HEARTBEAT_INTERVAL)


def run():
    state = {
        "status":        "starting",
        "restart_count": 0,
        "last_crash":    None,
        "last_error":    None,
        "bot_pid":       None,
    }

    # Bestehenden Restart-Count laden (falls Watchdog neu gestartet wird)
    if STATUS_FILE.exists():
        try:
            d = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
            state["restart_count"] = d.get("restart_count", 0)
        except Exception:
            pass

    # Heartbeat-Thread starten
    stop_hb = threading.Event()
    hb = threading.Thread(
        target=_heartbeat_thread, args=(state, stop_hb), daemon=True
    )
    hb.start()

    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[Watchdog {ts}] Gestartet — ueberwache: {BOT_SCRIPT.name}")
    print(f"[Watchdog {ts}] Stoppen: Strg+C\n")

    try:
        while True:
            state["status"] = "starting"
            ts = datetime.now().strftime("%H:%M:%S")
            print(f"[Watchdog {ts}] Starte Bot (Lauf #{state['restart_count'] + 1})...")

            try:
                proc = subprocess.Popen(
                    [sys.executable, str(BOT_SCRIPT)],
                    cwd=str(ROOT),
                )
                state["bot_pid"] = proc.pid
                state["status"]  = "running"
                write_status(**state)
                ts = datetime.now().strftime("%H:%M:%S")
                print(f"[Watchdog {ts}] Bot PID {proc.pid} laeuft.")

                proc.wait()  # blockiert bis Bot endet

                rc = proc.returncode
                state["last_crash"] = now_iso()
                state["bot_pid"]    = None

                ts = datetime.now().strftime("%H:%M:%S")
                if rc == 0:
                    print(f"[Watchdog {ts}] Bot normal beendet (Code 0).")
                else:
                    print(f"[Watchdog {ts}] !! Bot Absturz — Exit {rc}.")
                    state["last_error"] = f"Exit code {rc} um {state['last_crash']}"

            except Exception as e:
                state["last_crash"] = now_iso()
                state["last_error"] = str(e)
                state["bot_pid"]    = None
                ts = datetime.now().strftime("%H:%M:%S")
                print(f"[Watchdog {ts}] Fehler beim Starten: {e}")

            state["restart_count"] += 1
            state["status"] = "restarting"
            write_status(**state)

            ts = datetime.now().strftime("%H:%M:%S")
            print(f"[Watchdog {ts}] Neustart in {RESTART_DELAY} Sekunden...")
            time.sleep(RESTART_DELAY)

    except KeyboardInterrupt:
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"\n[Watchdog {ts}] Manuell gestoppt.")
        stop_hb.set()
        state["status"]  = "stopped"
        state["bot_pid"] = None
        write_status(**state)
        sys.exit(0)


if __name__ == "__main__":
    run()
