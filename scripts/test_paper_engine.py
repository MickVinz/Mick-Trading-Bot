"""
Tests für die Paper-Trading-Engine.

Alle Tests laufen ohne Netzwerkzugriff (synthetische Kerzen + Setups).
Kein Pytest nötig — direkt ausführbar:
    python scripts/test_paper_engine.py

Getestet werden:
    1.  check_exit — TP-Exit (Long)
    2.  check_exit — SL-Exit (Long)
    3.  check_exit — beide getroffen → SL gewinnt
    4.  check_exit — kein Treffer
    5.  check_exit — Short-Logik (gespiegelt)
    6.  Neustart ohne state.json → Balance = start_balance
    7.  Neustart mit state.json → Balance bleibt erhalten
    8.  Entry bei gültigem Setup auf letzter Kerze
    9.  Kein Doppel-Entry (zweite Kerze ohne neues Signal → keine neue Position)
    10. TP-Exit-Ablauf: Entry → TP-Kerze → Balance steigt
    11. SL-Exit-Ablauf: Entry → SL-Kerze → Balance sinkt
    12. -3%-Risk-Gate: kein Entry nach Tagesverlust
"""

import json
import sys
import tempfile
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.paper.journal import Journal
from src.paper.paper_engine import _make_decision, check_exit, size_qty
from src.paper.position import Position

# ---------------------------------------------------------------------------
# Hilfsfunktionen für synthetische Testdaten
# ---------------------------------------------------------------------------

_BASE_CONFIG = {
    "market": {"symbol": "BTC/USDT", "timeframe": "5m"},
    "paper_trading": {
        "start_balance": 1000.0,
        "risk_pct": 1.0,
        "leverage_cap": 3.0,
        "max_daily_loss_pct": 3.0,
    },
    # Minimale Felder, damit _make_decision nicht auf fehlende Keys stößt
}


def _ts(offset_minutes: int = 0) -> pd.Timestamp:
    """UTC-Timestamp als Basis für synthetische Kerzen."""
    base = pd.Timestamp("2026-01-01 10:00:00", tz="utc")
    return base + pd.Timedelta(minutes=offset_minutes)


def _candle(
    ts: pd.Timestamp,
    close: float = 100.0,
    high: float = 101.0,
    low: float = 99.0,
) -> pd.Series:
    """Minimale Kerze als pandas Series (nur Felder, die check_exit braucht)."""
    return pd.Series({
        "timestamp": ts,
        "open": close,
        "high": high,
        "low": low,
        "close": close,
        "volume": 10.0,
    })


def _make_df(candles: list[pd.Series]) -> pd.DataFrame:
    """Liste von Candle-Series → DataFrame."""
    return pd.DataFrame(candles).reset_index(drop=True)


def _make_setup(
    ts: pd.Timestamp,
    direction: str = "long",
    entry: float = 100.0,
    sl: float = 99.0,
    tp1: float = 102.0,
    setup_valid: bool = True,
) -> pd.DataFrame:
    """Minimales Setup-DataFrame (Format wie calculate_trade_levels-Ausgabe)."""
    return pd.DataFrame([{
        "time": ts,
        "direction": direction,
        "anchor_wt1": -80.0,
        "trigger_wt1": -65.0,
        "anchor_mfi": 30.0,
        "trigger_mfi": 45.0,
        "mfi_filter_passed": True,
        "divergence_active": False,
        "divergence_type": None,
        "warmup_artefact": False,
        "setup_valid": setup_valid,
        "entry": entry,
        "sl": sl,
        "tp1": tp1,
        "rr_ratio": 2.0,
        "sl_risiko_pct": abs(entry - sl) / entry * 100,
        "sl_zu_eng": False,
        "sl_quelle": "pivot",
    }])


def _make_journal(config: dict, data_dir: Path) -> Journal:
    """Journal mit isoliertem data_dir (kein Konflikt mit echten Daten)."""
    return Journal(config, data_dir=data_dir)


# ---------------------------------------------------------------------------
# Testfälle
# ---------------------------------------------------------------------------

_pass_count = 0
_fail_count = 0


def _assert(condition: bool, label: str) -> None:
    global _pass_count, _fail_count
    if condition:
        _pass_count += 1
        print(f"  ✓  {label}")
    else:
        _fail_count += 1
        print(f"  ✗  FEHLER: {label}")


# --- 1. check_exit: TP (Long) ---
def test_check_exit_tp_long():
    pos = Position(entry=100.0, sl=99.0, tp1=102.0, qty=1.0,
                   direction="long", entry_time=_ts(), divergence=False)
    candle = _candle(_ts(5), close=101.5, high=102.5, low=100.5)
    reason, price = check_exit(pos, candle)
    _assert(reason == "tp1", "1. check_exit TP Long: reason='tp1'")
    _assert(price == 102.0, "1. check_exit TP Long: price=tp1")


# --- 2. check_exit: SL (Long) ---
def test_check_exit_sl_long():
    pos = Position(entry=100.0, sl=99.0, tp1=102.0, qty=1.0,
                   direction="long", entry_time=_ts(), divergence=False)
    candle = _candle(_ts(5), close=98.5, high=99.5, low=98.5)
    reason, price = check_exit(pos, candle)
    _assert(reason == "sl", "2. check_exit SL Long: reason='sl'")
    _assert(price == 99.0, "2. check_exit SL Long: price=sl")


# --- 3. check_exit: beide → SL gewinnt ---
def test_check_exit_both_sl_wins():
    pos = Position(entry=100.0, sl=99.0, tp1=102.0, qty=1.0,
                   direction="long", entry_time=_ts(), divergence=False)
    # Kerze berührt sowohl low=98.5 (≤SL=99) als auch high=102.5 (≥TP=102)
    candle = _candle(_ts(5), close=100.0, high=102.5, low=98.5)
    reason, price = check_exit(pos, candle)
    _assert(reason == "sl", "3. check_exit Konflikt: SL gewinnt")
    _assert(price == 99.0, "3. check_exit Konflikt: Exit-Preis=SL")


# --- 4. check_exit: kein Treffer ---
def test_check_exit_no_hit():
    pos = Position(entry=100.0, sl=99.0, tp1=102.0, qty=1.0,
                   direction="long", entry_time=_ts(), divergence=False)
    candle = _candle(_ts(5), close=100.5, high=101.0, low=99.5)
    reason, price = check_exit(pos, candle)
    _assert(reason is None, "4. check_exit kein Treffer: reason=None")
    _assert(price is None, "4. check_exit kein Treffer: price=None")


# --- 5. check_exit: Short-Logik ---
def test_check_exit_short():
    pos = Position(entry=100.0, sl=101.0, tp1=98.0, qty=1.0,
                   direction="short", entry_time=_ts(), divergence=False)
    # TP: low ≤ tp1=98 → hit
    candle_tp = _candle(_ts(5), close=98.5, high=99.5, low=97.5)
    reason, price = check_exit(pos, candle_tp)
    _assert(reason == "tp1", "5. check_exit Short TP: reason='tp1'")

    # SL: high ≥ sl=101 → hit
    candle_sl = _candle(_ts(5), close=100.5, high=101.5, low=100.0)
    reason, price = check_exit(pos, candle_sl)
    _assert(reason == "sl", "5. check_exit Short SL: reason='sl'")


# --- 6. Neustart ohne state.json → Balance = start_balance ---
def test_state_fresh_start():
    with tempfile.TemporaryDirectory() as tmp:
        j = _make_journal(_BASE_CONFIG, Path(tmp))
        _assert(j.state["balance"] == 1000.0,
                "6. Fresh start: balance=1000")
        _assert(j.state["open_position"] is None,
                "6. Fresh start: keine offene Position")


# --- 7. Neustart mit state.json → Balance bleibt erhalten ---
def test_state_persist():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        j1 = _make_journal(_BASE_CONFIG, tmp_path)
        j1.state["balance"] = 1234.56
        j1.save_state()

        j2 = _make_journal(_BASE_CONFIG, tmp_path)
        _assert(abs(j2.state["balance"] - 1234.56) < 0.001,
                "7. Neustart: Balance bleibt erhalten (1234.56)")


# --- 8. Entry bei gültigem Setup auf letzter Kerze ---
def test_entry_valid_setup():
    with tempfile.TemporaryDirectory() as tmp:
        j = _make_journal(_BASE_CONFIG, Path(tmp))
        last_ts = _ts(0)
        df = _make_df([_candle(last_ts)])
        setups = _make_setup(last_ts, direction="long", entry=100.0, sl=99.0, tp1=102.0)

        result = _make_decision(df, setups, j, _BASE_CONFIG)
        _assert(result["entry_taken"], "8. Entry genommen bei gültigem Setup")
        _assert(result["setup_found"], "8. setup_found=True")
        _assert(j.state["open_position"] is not None, "8. Position in State")
        _assert(j.state["open_position"].direction == "long",
                "8. Position direction=long")


# --- 9. Kein Doppel-Entry (gleiche Kerze nochmals → kein neuer Entry) ---
def test_no_double_entry():
    with tempfile.TemporaryDirectory() as tmp:
        j = _make_journal(_BASE_CONFIG, Path(tmp))
        last_ts = _ts(0)
        df = _make_df([_candle(last_ts)])
        setups = _make_setup(last_ts)

        _make_decision(df, setups, j, _BASE_CONFIG)   # erster Durchlauf: Entry

        # Zweiter Durchlauf mit GLEICHER Kerze → Dedup greift
        result2 = _make_decision(df, setups, j, _BASE_CONFIG)
        _assert(not result2["entry_taken"],
                "9. Kein Doppel-Entry bei gleicher Kerze (Dedup)")


# --- 10. TP-Exit-Ablauf: Balance steigt ---
def test_tp_exit_full_flow():
    with tempfile.TemporaryDirectory() as tmp:
        j = _make_journal(_BASE_CONFIG, Path(tmp))

        # Kerze 0: Entry-Signal
        ts0 = _ts(0)
        df0 = _make_df([_candle(ts0, close=100.0)])
        setups = _make_setup(ts0, entry=100.0, sl=99.0, tp1=102.0)
        _make_decision(df0, setups, j, _BASE_CONFIG)

        balance_after_entry = j.state["balance"]
        pos = j.state["open_position"]
        _assert(pos is not None, "10. Position nach Entry geöffnet")

        # Kerze 1: TP getroffen (high=102.5 ≥ tp1=102)
        ts1 = _ts(5)
        df1 = _make_df([_candle(ts0), _candle(ts1, close=101.5, high=102.5, low=100.5)])
        result = _make_decision(df1, pd.DataFrame(), j, _BASE_CONFIG)

        _assert(result["exit_reason"] == "tp1", "10. Exit via TP1")
        _assert(j.state["open_position"] is None, "10. Position geschlossen")
        _assert(j.state["balance"] > balance_after_entry,
                "10. Balance nach TP-Exit höher als Einstieg")
        _assert(j._trades_path.exists(), "10. trades.csv angelegt")


# --- 11. SL-Exit-Ablauf: Balance sinkt ---
def test_sl_exit_full_flow():
    with tempfile.TemporaryDirectory() as tmp:
        j = _make_journal(_BASE_CONFIG, Path(tmp))

        ts0 = _ts(0)
        df0 = _make_df([_candle(ts0, close=100.0)])
        setups = _make_setup(ts0, entry=100.0, sl=99.0, tp1=102.0)
        _make_decision(df0, setups, j, _BASE_CONFIG)

        start_bal = j.state["balance"]

        ts1 = _ts(5)
        df1 = _make_df([_candle(ts0), _candle(ts1, close=98.5, high=99.0, low=98.0)])
        result = _make_decision(df1, pd.DataFrame(), j, _BASE_CONFIG)

        _assert(result["exit_reason"] == "sl", "11. Exit via SL")
        _assert(j.state["balance"] < start_bal, "11. Balance nach SL-Exit gesunken")


# --- 12. -3%-Risk-Gate ---
def test_risk_gate():
    with tempfile.TemporaryDirectory() as tmp:
        j = _make_journal(_BASE_CONFIG, Path(tmp))

        # Manuell Tagesverlust ≥ -3% setzen
        today = pd.Timestamp.now(tz="utc").strftime("%Y-%m-%d")
        j.state["day"] = today
        j.state["day_start_balance"] = 1000.0
        j.state["realized_pnl_today"] = -30.0   # -3% exakt → Gate aktiv

        last_ts = _ts(0)
        df = _make_df([_candle(last_ts)])
        setups = _make_setup(last_ts)

        result = _make_decision(df, setups, j, _BASE_CONFIG)
        _assert(not result["entry_taken"],
                "12. Risk-Gate: kein Entry bei -3% Tagesverlust")
        _assert(j.state["open_position"] is None,
                "12. Risk-Gate: keine offene Position")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def main() -> None:
    print("Paper-Engine-Tests\n")

    test_check_exit_tp_long()
    test_check_exit_sl_long()
    test_check_exit_both_sl_wins()
    test_check_exit_no_hit()
    test_check_exit_short()
    test_state_fresh_start()
    test_state_persist()
    test_entry_valid_setup()
    test_no_double_entry()
    test_tp_exit_full_flow()
    test_sl_exit_full_flow()
    test_risk_gate()

    print(f"\n{_pass_count + _fail_count} Tests: "
          f"{_pass_count} bestanden, {_fail_count} fehlgeschlagen.")
    if _fail_count > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
