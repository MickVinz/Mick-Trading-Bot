"""
Tests fuer das Multi-Coin-Journal (positions-Dict, symbol in trades.csv, Migration).
Direkt ausfuehrbar: python scripts/test_journal_multicoin.py
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
from src.paper.position import Position

_pass = 0
_fail = 0


def _assert(cond, label):
    global _pass, _fail
    if cond:
        _pass += 1
        print(f"  ✓  {label}")
    else:
        _fail += 1
        print(f"  ✗  FEHLER: {label}")


_CONFIG = {
    "market": {"symbols": ["BTC/USDT", "ETH/USDT"], "timeframe": "5m"},
    "paper_trading": {"start_balance": 1000.0},
}


def _pos():
    return Position(entry=100.0, sl=99.0, tp1=102.0, qty=1.0,
                    direction="long", entry_time=pd.Timestamp("2026-01-01T10:00:00Z"),
                    divergence=False)


def test_fresh_state_has_positions_dict():
    with tempfile.TemporaryDirectory() as tmp:
        j = Journal(_CONFIG, data_dir=Path(tmp))
        _assert(j.state["balance"] == 1000.0, "1. fresh: balance=1000")
        _assert(j.state["positions"] == {}, "1. fresh: leeres positions-Dict")
        _assert(j.get_position("BTC/USDT") is None, "1. get_position: flach=None")


def test_set_and_persist_position_per_symbol():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        j1 = Journal(_CONFIG, data_dir=tmp_path)
        j1.set_position("ETH/USDT", _pos())
        j1.save_state()

        j2 = Journal(_CONFIG, data_dir=tmp_path)
        eth = j2.get_position("ETH/USDT")
        _assert(eth is not None and eth.direction == "long",
                "2. Position pro Symbol bleibt ueber Neustart erhalten")
        _assert(j2.get_position("BTC/USDT") is None,
                "2. anderes Symbol bleibt flach")


def test_last_candle_time_per_symbol():
    with tempfile.TemporaryDirectory() as tmp:
        j = Journal(_CONFIG, data_dir=Path(tmp))
        j.set_last_candle_time("BTC/USDT", "2026-01-01T10:00:00+00:00")
        _assert(j.get_last_candle_time("BTC/USDT") == "2026-01-01T10:00:00+00:00",
                "3. last_candle_time pro Symbol gesetzt/gelesen")
        _assert(j.get_last_candle_time("ETH/USDT") is None,
                "3. last_candle_time anderes Symbol = None")


def test_record_trade_writes_symbol_column():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        j = Journal(_CONFIG, data_dir=tmp_path)
        j.record_trade(symbol="ETH/USDT", position=_pos(),
                       exit_time=pd.Timestamp("2026-01-01T10:30:00Z"),
                       exit_price=102.0, exit_reason="tp1", balance_after=1002.0)
        rows = (tmp_path / "trades.csv").read_text(encoding="utf-8").splitlines()
        _assert(rows[0].startswith("symbol,"), "4. trades.csv: symbol ist erste Spalte")
        _assert(rows[1].startswith("ETH/USDT,"), "4. trades.csv: Zeile beginnt mit Symbol")


def test_migration_v1_to_v2():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        # Altes v1-state.json (single open_position, single last_candle_time)
        old_state = {
            "balance": 1234.0,
            "open_position": _pos().to_dict(),
            "day_start_balance": 1234.0,
            "day": "2026-01-01",
            "realized_pnl_today": -5.0,
            "last_candle_time": "2026-01-01T10:00:00+00:00",
        }
        (tmp_path / "state.json").write_text(json.dumps(old_state), encoding="utf-8")

        j = Journal(_CONFIG, data_dir=tmp_path)
        _assert(j.state["balance"] == 1234.0, "5. Migration: balance uebernommen")
        _assert("open_position" not in j.state, "5. Migration: altes open_position entfernt")
        btc = j.get_position("BTC/USDT")
        _assert(btc is not None and btc.entry == 100.0,
                "5. Migration: alte Position -> positions['BTC/USDT']")
        _assert(j.get_last_candle_time("BTC/USDT") == "2026-01-01T10:00:00+00:00",
                "5. Migration: last_candle_time -> BTC/USDT")
        _assert((tmp_path / "state.json.bak").exists(),
                "5. Migration: Backup state.json.bak angelegt")


def main():
    print("Journal-Multi-Coin-Tests\n")
    test_fresh_state_has_positions_dict()
    test_set_and_persist_position_per_symbol()
    test_last_candle_time_per_symbol()
    test_record_trade_writes_symbol_column()
    test_migration_v1_to_v2()
    print(f"\n{_pass + _fail} Tests: {_pass} bestanden, {_fail} fehlgeschlagen.")
    if _fail > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
