"""
watchdog.py — startet run_paper_loop.py und startet bei Absturz automatisch neu.
Schreibt data/watchdog_status.json (Dashboard) und data/watchdog.log (Diagnose).

Manuell starten : python scripts/watchdog.py
Hintergrund     : pythonw.exe scripts/watchdog.py  (kein Fenster noetig)
Stoppen         : Strg+C  oder  Stop-ScheduledTask -TaskName MickBot-Watchdog
"""
import subprocess
import sys
import time
import json
import threading
import os
from pathlib import Path
from datetime import datetime, timezone

ROOT        = Path(__file__).parent.parent
STATUS_FILE = ROOT / "data" / "watchdog_status.json"
LOG_FILE    = ROOT / "data" / "watchdog.log"
BOT_LOG     = ROOT / "data" / "bot.log"
BOT_SCRIPT  = ROOT / "scripts" / "run_paper_loop.py"
LOCK_FILE   = ROOT / "data" / "watchdog.lock"

RESTART_DELAY      = 10   # Sekunden warten vor Neustart
HEARTBEAT_INTERVAL = 30   # Sekunden zwischen Heartbeat-Updates
LOG_MAX_BYTES      = 512 * 1024  # 512 KB — danach rotieren


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def log(msg: str):
    """Schreibt in Log-Datei UND Konsole (falls vorhanden)."""
    ts   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}\n"
    try:
        ROOT.joinpath("data").mkdir(exist_ok=True)
        # Log rotieren wenn zu gross
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > LOG_MAX_BYTES:
            LOG_FILE.rename(LOG_FILE.with_suffix(".log.bak"))
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass
    try:
        print(line, end="", flush=True)
    except Exception:
        pass  # kein Konsolen-Fenster (pythonw.exe) — ignorieren


def write_status(status, restart_count, crash_count=0, last_crash=None, bot_pid=None, last_error=None):
    STATUS_FILE.parent.mkdir(exist_ok=True)
    STATUS_FILE.write_text(json.dumps({
        "watchdog_alive": now_iso(),
        "bot_status":    status,
        "restart_count": restart_count,
        "crash_count":   crash_count,
        "last_crash":    last_crash,
        "last_error":    last_error,
        "bot_pid":       bot_pid,
    }, indent=2, ensure_ascii=False), encoding="utf-8")


def _heartbeat_thread(state: dict, stop_event: threading.Event):
    while not stop_event.is_set():
        write_status(
            state["status"],
            state["restart_count"],
            crash_count=state["crash_count"],
            last_crash=state["last_crash"],
            bot_pid=state["bot_pid"],
        )
        stop_event.wait(HEARTBEAT_INTERVAL)


def run():
    # Nur eine Instanz erlaubt
    LOCK_FILE.parent.mkdir(exist_ok=True)
    try:
        lock_fd = open(LOCK_FILE, "x")
        lock_fd.write(str(os.getpid()))
        lock_fd.close()
    except FileExistsError:
        existing_pid = LOCK_FILE.read_text().strip()
        try:
            os.kill(int(existing_pid), 0)  # 0 = nur Existenzcheck
            log(f"Watchdog laeuft bereits (PID {existing_pid}). Beende.")
            sys.exit(0)
        except (OSError, ValueError):
            # Alter Prozess tot — Lock übernehmen
            log(f"Altes Lock (PID {existing_pid}) war verwaist. Uebernehme.")
            LOCK_FILE.write_text(str(os.getpid()))

    state = {
        "status":        "starting",
        "restart_count": 0,
        "crash_count":   0,
        "last_crash":    None,
        "last_error":    None,
        "bot_pid":       None,
    }

    if STATUS_FILE.exists():
        try:
            d = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
            state["restart_count"] = d.get("restart_count", 0)
        except Exception:
            pass

    stop_hb = threading.Event()
    hb = threading.Thread(
        target=_heartbeat_thread, args=(state, stop_hb), daemon=True
    )
    hb.start()

    # python.exe fuer Bot-Subprocess — pythonw.exe wuerde auch gehen
    python_exe = Path(sys.executable)
    if python_exe.stem.lower() == "pythonw":
        bot_python = python_exe.parent / "python.exe"
        if not bot_python.exists():
            bot_python = python_exe
    else:
        bot_python = python_exe

    log(f"Watchdog gestartet — ueberwache: {BOT_SCRIPT.name}")
    log(f"Python: {bot_python}")

    try:
        while True:
            state["status"] = "starting"
            log(f"Starte Bot (Lauf #{state['restart_count'] + 1})...")

            try:
                # Bot-Log rotieren wenn zu gross
                if BOT_LOG.exists() and BOT_LOG.stat().st_size > LOG_MAX_BYTES:
                    BOT_LOG.rename(BOT_LOG.with_suffix(".log.bak"))
                BOT_LOG.parent.mkdir(exist_ok=True)

                with BOT_LOG.open("a", encoding="utf-8") as bot_log_f:
                    sep = "=" * 52
                    ts_now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    bot_log_f.write(f"\n{sep}\n[Watchdog] Lauf #{state['restart_count']+1} — {ts_now}\n{sep}\n")
                    bot_log_f.flush()

                    proc = subprocess.Popen(
                        [str(bot_python), "-u", str(BOT_SCRIPT)],
                        cwd=str(ROOT),
                        stdout=bot_log_f,
                        stderr=bot_log_f,
                        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW,
                    )
                    state["bot_pid"] = proc.pid
                    state["status"]  = "running"
                    write_status(**state)
                    log(f"Bot PID {proc.pid} laeuft — Output -> data/bot.log")

                    proc.wait()

                rc = proc.returncode
                state["last_crash"] = now_iso()
                state["bot_pid"]    = None

                if rc == 0:
                    log("Bot normal beendet (Code 0).")
                else:
                    log(f"!! Bot Absturz — Exit {rc}.")
                    state["crash_count"] += 1
                    state["last_error"] = f"Exit code {rc} um {state['last_crash']}"

            except Exception as e:
                state["last_crash"] = now_iso()
                state["last_error"] = str(e)
                state["bot_pid"]    = None
                state["crash_count"] += 1
                log(f"Fehler beim Starten: {e}")

            state["restart_count"] += 1
            state["status"] = "restarting"
            write_status(**state)
            log(f"Neustart in {RESTART_DELAY} Sekunden...")
            time.sleep(RESTART_DELAY)

    except KeyboardInterrupt:
        log("Manuell gestoppt.")
    finally:
        stop_hb.set()
        state["status"]  = "stopped"
        state["bot_pid"] = None
        write_status(**state)
        try:
            LOCK_FILE.unlink(missing_ok=True)
        except Exception:
            pass
        sys.exit(0)


if __name__ == "__main__":
    run()
