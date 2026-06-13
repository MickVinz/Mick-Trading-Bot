"""
Tests für die Paper-Trading-Engine (Multi-Coin).

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
    12. Risk-Gate (gate_open=False) → kein Entry
    13. run_cycle: zwei Coins, Exit BTC + Entry ETH im selben Zyklus
    14. run_cycle: kein Parallel-Limit — beide Coins gleichzeitig offen
    15. run_cycle: geteiltes Tageslimit pausiert ALLE Entries
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
from src.paper.paper_engine import (
    _book_notional, _decide_symbol, _gate_open, check_exit, exit_costs,
    run_cycle, size_qty,
)
from src.paper.position import Position

# ---------------------------------------------------------------------------
# Hilfsfunktionen für synthetische Testdaten
# ---------------------------------------------------------------------------

_BASE_CONFIG = {
    "market": {"symbols": ["BTC/USDT"], "timeframe": "5m"},
    "paper_trading": {
        "start_balance": 1000.0,
        "risk_pct": 1.0,
        "leverage_cap": 3.0,
        "max_daily_loss_pct": 3.0,
    },
}

# Wie _BASE_CONFIG, aber mit Gebuehren + Slippage (BingX-realistisch).
_FEE_CONFIG = {
    "market": {"symbols": ["BTC/USDT"], "timeframe": "5m"},
    "paper_trading": {
        "start_balance": 1000.0,
        "risk_pct": 1.0,
        "leverage_cap": 3.0,
        "max_daily_loss_pct": 10.0,
        "taker_fee_pct": 0.05,   # 0,05 % pro Seite
        "slippage_pct": 0.02,    # 0,02 % pro Market-Fill
    },
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
    candle_tp = _candle(_ts(5), close=98.5, high=99.5, low=97.5)
    reason, price = check_exit(pos, candle_tp)
    _assert(reason == "tp1", "5. check_exit Short TP: reason='tp1'")

    candle_sl = _candle(_ts(5), close=100.5, high=101.5, low=100.0)
    reason, price = check_exit(pos, candle_sl)
    _assert(reason == "sl", "5. check_exit Short SL: reason='sl'")


# --- 6. Neustart ohne state.json → Balance = start_balance ---
def test_state_fresh_start():
    with tempfile.TemporaryDirectory() as tmp:
        j = _make_journal(_BASE_CONFIG, Path(tmp))
        _assert(j.state["balance"] == 1000.0, "6. Fresh start: balance=1000")
        _assert(j.get_position("BTC/USDT") is None,
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

        result = _decide_symbol("BTC/USDT", df, setups, j, _BASE_CONFIG, gate_open=True)
        _assert(result["entry_taken"], "8. Entry genommen bei gültigem Setup")
        _assert(result["setup_found"], "8. setup_found=True")
        _assert(j.get_position("BTC/USDT") is not None, "8. Position in State")
        _assert(j.get_position("BTC/USDT").direction == "long",
                "8. Position direction=long")


# --- 9. Kein Doppel-Entry (gleiche Kerze nochmals → kein neuer Entry) ---
def test_no_double_entry():
    with tempfile.TemporaryDirectory() as tmp:
        j = _make_journal(_BASE_CONFIG, Path(tmp))
        last_ts = _ts(0)
        df = _make_df([_candle(last_ts)])
        setups = _make_setup(last_ts)

        _decide_symbol("BTC/USDT", df, setups, j, _BASE_CONFIG, gate_open=True)
        result2 = _decide_symbol("BTC/USDT", df, setups, j, _BASE_CONFIG, gate_open=True)
        _assert(not result2["entry_taken"],
                "9. Kein Doppel-Entry bei gleicher Kerze (Dedup)")


# --- 10. TP-Exit-Ablauf: Balance steigt ---
def test_tp_exit_full_flow():
    with tempfile.TemporaryDirectory() as tmp:
        j = _make_journal(_BASE_CONFIG, Path(tmp))

        ts0 = _ts(0)
        df0 = _make_df([_candle(ts0, close=100.0)])
        setups = _make_setup(ts0, entry=100.0, sl=99.0, tp1=102.0)
        _decide_symbol("BTC/USDT", df0, setups, j, _BASE_CONFIG, gate_open=True)

        balance_after_entry = j.state["balance"]
        pos = j.get_position("BTC/USDT")
        _assert(pos is not None, "10. Position nach Entry geöffnet")

        ts1 = _ts(5)
        df1 = _make_df([_candle(ts0), _candle(ts1, close=101.5, high=102.5, low=100.5)])
        result = _decide_symbol("BTC/USDT", df1, pd.DataFrame(), j, _BASE_CONFIG, gate_open=True)

        _assert(result["exit_reason"] == "tp1", "10. Exit via TP1")
        _assert(j.get_position("BTC/USDT") is None, "10. Position geschlossen")
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
        _decide_symbol("BTC/USDT", df0, setups, j, _BASE_CONFIG, gate_open=True)

        start_bal = j.state["balance"]

        ts1 = _ts(5)
        df1 = _make_df([_candle(ts0), _candle(ts1, close=98.5, high=99.0, low=98.0)])
        result = _decide_symbol("BTC/USDT", df1, pd.DataFrame(), j, _BASE_CONFIG, gate_open=True)

        _assert(result["exit_reason"] == "sl", "11. Exit via SL")
        _assert(j.state["balance"] < start_bal, "11. Balance nach SL-Exit gesunken")


# --- 12. Risk-Gate: gate_open=False → kein Entry ---
def test_risk_gate():
    with tempfile.TemporaryDirectory() as tmp:
        j = _make_journal(_BASE_CONFIG, Path(tmp))

        today = pd.Timestamp.now(tz="utc").strftime("%Y-%m-%d")
        j.state["day"] = today
        j.state["day_start_balance"] = 1000.0
        j.state["realized_pnl_today"] = -30.0   # -3% exakt → Gate aktiv

        gate = _gate_open(j.state, _BASE_CONFIG)
        _assert(not gate, "12. _gate_open bei -3% Tagesverlust = False")

        last_ts = _ts(0)
        df = _make_df([_candle(last_ts)])
        setups = _make_setup(last_ts)

        result = _decide_symbol("BTC/USDT", df, setups, j, _BASE_CONFIG, gate_open=gate)
        _assert(not result["entry_taken"],
                "12. Risk-Gate: kein Entry bei geschlossenem Gate")
        _assert(j.get_position("BTC/USDT") is None,
                "12. Risk-Gate: keine offene Position")


# --- 13. run_cycle: zwei Coins, Exit BTC + Entry ETH im selben Zyklus ---
def test_run_cycle_two_coins():
    with tempfile.TemporaryDirectory() as tmp:
        cfg = {
            "market": {"symbols": ["BTC/USDT", "ETH/USDT"], "timeframe": "5m"},
            "paper_trading": {"start_balance": 1000.0, "risk_pct": 1.0,
                              "leverage_cap": 3.0, "max_daily_loss_pct": 10.0},
        }
        j = _make_journal(cfg, Path(tmp))

        j.set_position("BTC/USDT", Position(entry=100.0, sl=99.0, tp1=102.0, qty=1.0,
                       direction="long", entry_time=_ts(-5), divergence=False))
        last = _ts(0)

        per_symbol = {
            "BTC/USDT": (_make_df([_candle(last, close=101.5, high=102.5, low=100.5)]),
                         pd.DataFrame()),
            "ETH/USDT": (_make_df([_candle(last, close=100.0)]),
                         _make_setup(last, direction="long", entry=100.0, sl=99.0, tp1=102.0)),
        }
        result = run_cycle(j, cfg, _per_symbol=per_symbol)

        _assert(j.get_position("BTC/USDT") is None, "13. BTC per TP geschlossen")
        _assert(j.get_position("ETH/USDT") is not None, "13. ETH-Entry genommen")
        _assert(result["BTC/USDT"]["exit_reason"] == "tp1", "13. BTC exit_reason=tp1")
        _assert(result["ETH/USDT"]["entry_taken"], "13. ETH entry_taken=True")


# --- 14. run_cycle: kein Parallel-Limit — beide Coins gleichzeitig offen ---
def test_run_cycle_no_parallel_limit():
    with tempfile.TemporaryDirectory() as tmp:
        cfg = {
            "market": {"symbols": ["BTC/USDT", "ETH/USDT"], "timeframe": "5m"},
            "paper_trading": {"start_balance": 1000.0, "risk_pct": 1.0,
                              "leverage_cap": 3.0, "max_daily_loss_pct": 10.0},
        }
        j = _make_journal(cfg, Path(tmp))
        last = _ts(0)
        per_symbol = {
            "BTC/USDT": (_make_df([_candle(last, close=100.0)]),
                         _make_setup(last, "long", 100.0, 99.0, 102.0)),
            "ETH/USDT": (_make_df([_candle(last, close=100.0)]),
                         _make_setup(last, "long", 100.0, 99.0, 102.0)),
        }
        run_cycle(j, cfg, _per_symbol=per_symbol)
        _assert(j.get_position("BTC/USDT") is not None
                and j.get_position("ETH/USDT") is not None,
                "14. Beide Coins gleichzeitig offen (kein Parallel-Limit)")


# --- 15. run_cycle: geteiltes Tageslimit pausiert ALLE Entries ---
def test_run_cycle_shared_daily_gate():
    with tempfile.TemporaryDirectory() as tmp:
        cfg = {
            "market": {"symbols": ["BTC/USDT", "ETH/USDT"], "timeframe": "5m"},
            "paper_trading": {"start_balance": 1000.0, "risk_pct": 1.0,
                              "leverage_cap": 3.0, "max_daily_loss_pct": 10.0},
        }
        j = _make_journal(cfg, Path(tmp))
        today = pd.Timestamp.now(tz="utc").strftime("%Y-%m-%d")
        j.state["day"] = today
        j.state["day_start_balance"] = 1000.0
        j.state["realized_pnl_today"] = -100.0   # -10% exakt → Gate aktiv
        last = _ts(0)
        per_symbol = {
            "BTC/USDT": (_make_df([_candle(last)]), _make_setup(last)),
            "ETH/USDT": (_make_df([_candle(last)]), _make_setup(last)),
        }
        run_cycle(j, cfg, _per_symbol=per_symbol)
        _assert(j.get_position("BTC/USDT") is None
                and j.get_position("ETH/USDT") is None,
                "15. Geteiltes Tageslimit -10% pausiert alle Entries")


# --- 16. exit_costs: Einheiten-Kosten (Taker beide Legs + Slippage) ---
def test_exit_costs_unit():
    pos = Position(entry=100.0, sl=99.0, tp1=102.0, qty=1.0,
                   direction="long", entry_time=_ts(), divergence=False)
    pt = {"taker_fee_pct": 0.05, "slippage_pct": 0.02}

    # SL = Market-Fill: entry_fee 0.05 + exit_fee 0.0495 + entry_slip 0.02 + exit_slip 0.0198
    cost_sl = exit_costs(pos, 99.0, "sl", pt)
    _assert(abs(cost_sl - 0.1393) < 1e-6,
            "16. exit_costs SL = 0.1393 (Taker beide + Slippage beide)")

    # TP = Limit-Fill: KEIN Exit-Slippage → entry_fee 0.05 + exit_fee 0.051 + entry_slip 0.02
    cost_tp = exit_costs(pos, 102.0, "tp1", pt)
    _assert(abs(cost_tp - 0.121) < 1e-6,
            "16. exit_costs TP = 0.121 (kein Exit-Slippage, Limit)")

    # Ohne Gebuehren-Config → 0 (fee-freie Regression)
    _assert(exit_costs(pos, 99.0, "sl", {}) == 0.0,
            "16. exit_costs ohne Config = 0")


# --- 17. TP-Exit mit Gebuehren: Netto-Gewinn < Brutto ---
def test_tp_exit_with_fees():
    with tempfile.TemporaryDirectory() as tmp:
        j = _make_journal(_FEE_CONFIG, Path(tmp))
        ts0 = _ts(0)
        df0 = _make_df([_candle(ts0, close=100.0)])
        setups = _make_setup(ts0, entry=100.0, sl=99.0, tp1=102.0)
        _decide_symbol("BTC/USDT", df0, setups, j, _FEE_CONFIG, gate_open=True)

        # qty=10 (Risiko 10 / SL-Abstand 1). Brutto-TP=20, Kosten=1.21 → Netto 18.79
        ts1 = _ts(5)
        df1 = _make_df([_candle(ts0), _candle(ts1, close=101.5, high=102.5, low=100.5)])
        result = _decide_symbol("BTC/USDT", df1, pd.DataFrame(), j, _FEE_CONFIG, gate_open=True)

        _assert(result["exit_reason"] == "tp1", "17. Fee-TP: Exit via TP1")
        net = j.state["balance"] - 1000.0
        _assert(abs(net - 18.79) < 0.01,
                "17. Fee-TP: Netto +18.79 (< brutto +20 wegen Gebuehren)")


# --- 18. SL-Exit mit Gebuehren: Netto-Verlust > Brutto ---
def test_sl_exit_with_fees():
    with tempfile.TemporaryDirectory() as tmp:
        j = _make_journal(_FEE_CONFIG, Path(tmp))
        ts0 = _ts(0)
        df0 = _make_df([_candle(ts0, close=100.0)])
        setups = _make_setup(ts0, entry=100.0, sl=99.0, tp1=102.0)
        _decide_symbol("BTC/USDT", df0, setups, j, _FEE_CONFIG, gate_open=True)

        # Brutto-SL=-10, Kosten=1.393 → Netto -11.393
        ts1 = _ts(5)
        df1 = _make_df([_candle(ts0), _candle(ts1, close=98.5, high=99.0, low=98.0)])
        result = _decide_symbol("BTC/USDT", df1, pd.DataFrame(), j, _FEE_CONFIG, gate_open=True)

        _assert(result["exit_reason"] == "sl", "18. Fee-SL: Exit via SL")
        net_loss = 1000.0 - j.state["balance"]
        _assert(abs(net_loss - 11.393) < 0.01,
                "18. Fee-SL: Netto -11.393 (> brutto -10 wegen Gebuehren)")


# --- 19. _book_notional: Summe qty × entry ueber alle offenen Positionen ---
def test_book_notional():
    with tempfile.TemporaryDirectory() as tmp:
        cfg = {
            "market": {"symbols": ["BTC/USDT", "ETH/USDT"], "timeframe": "5m"},
            "paper_trading": {"start_balance": 1000.0},
        }
        j = _make_journal(cfg, Path(tmp))
        _assert(_book_notional(j) == 0.0, "19. _book_notional leer = 0")
        j.set_position("BTC/USDT", Position(entry=100.0, sl=99.0, tp1=102.0, qty=2.0,
                       direction="long", entry_time=_ts(), divergence=False))
        j.set_position("ETH/USDT", Position(entry=50.0, sl=49.0, tp1=52.0, qty=3.0,
                       direction="long", entry_time=_ts(), divergence=False))
        # 2*100 + 3*50 = 350
        _assert(_book_notional(j) == 350.0, "19. _book_notional = 350 (200+150)")


# --- 20. Buch-Notional-Cap blockiert weiteren Entry ---
def test_book_notional_cap_blocks():
    with tempfile.TemporaryDirectory() as tmp:
        cfg = {
            "market": {"symbols": ["BTC/USDT", "ETH/USDT"], "timeframe": "5m"},
            "paper_trading": {"start_balance": 1000.0, "risk_pct": 1.0,
                              "leverage_cap": 3.0, "max_daily_loss_pct": 10.0,
                              "max_book_notional_x": 1.5},  # Cap = 1500 USDT Notional
        }
        j = _make_journal(cfg, Path(tmp))
        last = _ts(0)
        # Jeder Entry: entry 100, sl 99 -> qty 10 -> Notional 1000.
        # BTC: 1000 <= 1500 -> oeffnet. ETH: 1000+1000=2000 > 1500 -> blockiert.
        per_symbol = {
            "BTC/USDT": (_make_df([_candle(last, close=100.0)]),
                         _make_setup(last, "long", 100.0, 99.0, 102.0)),
            "ETH/USDT": (_make_df([_candle(last, close=100.0)]),
                         _make_setup(last, "long", 100.0, 99.0, 102.0)),
        }
        run_cycle(j, cfg, _per_symbol=per_symbol)
        _assert(j.get_position("BTC/USDT") is not None,
                "20. BTC geoeffnet (unter Cap)")
        _assert(j.get_position("ETH/USDT") is None,
                "20. ETH blockiert (Buch-Notional-Cap erreicht)")


# --- 21. Ohne Cap-Config: beide oeffnen (Abwaertskompat) ---
def test_book_notional_cap_disabled():
    with tempfile.TemporaryDirectory() as tmp:
        cfg = {
            "market": {"symbols": ["BTC/USDT", "ETH/USDT"], "timeframe": "5m"},
            "paper_trading": {"start_balance": 1000.0, "risk_pct": 1.0,
                              "leverage_cap": 3.0, "max_daily_loss_pct": 10.0},
            # kein max_book_notional_x -> Cap aus
        }
        j = _make_journal(cfg, Path(tmp))
        last = _ts(0)
        per_symbol = {
            "BTC/USDT": (_make_df([_candle(last, close=100.0)]),
                         _make_setup(last, "long", 100.0, 99.0, 102.0)),
            "ETH/USDT": (_make_df([_candle(last, close=100.0)]),
                         _make_setup(last, "long", 100.0, 99.0, 102.0)),
        }
        run_cycle(j, cfg, _per_symbol=per_symbol)
        _assert(j.get_position("BTC/USDT") is not None
                and j.get_position("ETH/USDT") is not None,
                "21. Ohne Cap: beide oeffnen (Abwaertskompat)")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def main() -> None:
    print("Paper-Engine-Tests (Multi-Coin)\n")

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
    test_run_cycle_two_coins()
    test_run_cycle_no_parallel_limit()
    test_run_cycle_shared_daily_gate()
    test_exit_costs_unit()
    test_tp_exit_with_fees()
    test_sl_exit_with_fees()
    test_book_notional()
    test_book_notional_cap_blocks()
    test_book_notional_cap_disabled()

    print(f"\n{_pass_count + _fail_count} Tests: "
          f"{_pass_count} bestanden, {_fail_count} fehlgeschlagen.")
    if _fail_count > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
