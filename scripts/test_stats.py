"""
Tests fuer das Statistik-Modul (src/paper/stats.py).
Direkt ausfuehrbar: python scripts/test_stats.py

Reine Mathematik, kein Netzwerk, kein State.
"""
import sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.paper.stats import compute_stats

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


# Drei Trades mit bekannten Werten (kumulative balance_after ab Start 1000):
#   t1 BTC long  +18.79  (1018.79)  fee 1.21
#   t2 ETH short -11.39  (1007.40)  fee 1.39
#   t3 BTC long  +18.79  (1026.19)  fee 1.21
_TRADES = [
    {"symbol": "BTC/USDT", "direction": "long",  "pnl_usd": "18.79",
     "fees_usd": "1.21", "balance_after": "1018.79"},
    {"symbol": "ETH/USDT", "direction": "short", "pnl_usd": "-11.39",
     "fees_usd": "1.39", "balance_after": "1007.40"},
    {"symbol": "BTC/USDT", "direction": "long",  "pnl_usd": "18.79",
     "fees_usd": "1.21", "balance_after": "1026.19"},
]


def test_empty():
    s = compute_stats([], start_balance=1000.0)
    _assert(s["total_trades"] == 0, "1. leer: total_trades=0")
    _assert(s["win_rate_pct"] == 0.0, "1. leer: win_rate=0")
    _assert(s["net_pnl"] == 0.0, "1. leer: net_pnl=0")
    _assert(s["profit_factor"] is None, "1. leer: profit_factor=None")
    _assert(s["max_drawdown_pct"] == 0.0, "1. leer: max_drawdown=0")


def test_basic_counts():
    s = compute_stats(_TRADES, start_balance=1000.0)
    _assert(s["total_trades"] == 3, "2. total_trades=3")
    _assert(s["wins"] == 2, "2. wins=2")
    _assert(s["losses"] == 1, "2. losses=1")
    _assert(abs(s["win_rate_pct"] - 66.667) < 0.01, "2. win_rate=66.67%")


def test_pnl_and_fees():
    s = compute_stats(_TRADES, start_balance=1000.0)
    _assert(abs(s["net_pnl"] - 26.19) < 0.001, "3. net_pnl=26.19")
    _assert(abs(s["total_fees"] - 3.81) < 0.001, "3. total_fees=3.81")
    _assert(abs(s["gross_pnl"] - 30.0) < 0.001, "3. gross_pnl=30.00 (netto+fees)")


def test_averages_expectancy():
    s = compute_stats(_TRADES, start_balance=1000.0)
    _assert(abs(s["avg_win"] - 18.79) < 0.001, "4. avg_win=18.79")
    _assert(abs(s["avg_loss"] - (-11.39)) < 0.001, "4. avg_loss=-11.39")
    _assert(abs(s["expectancy"] - 8.73) < 0.01, "4. expectancy=8.73 (net/trades)")


def test_profit_factor():
    s = compute_stats(_TRADES, start_balance=1000.0)
    # (18.79+18.79) / 11.39 = 3.299
    _assert(abs(s["profit_factor"] - 3.299) < 0.01, "5. profit_factor=3.30")


def test_profit_factor_no_losses():
    only_wins = [{"symbol": "BTC/USDT", "direction": "long", "pnl_usd": "5.0",
                  "fees_usd": "0.5", "balance_after": "1005.0"}]
    s = compute_stats(only_wins, start_balance=1000.0)
    _assert(s["profit_factor"] is None, "6. profit_factor=None ohne Verlierer (kein inf)")


def test_max_drawdown():
    s = compute_stats(_TRADES, start_balance=1000.0)
    # Equity [1000, 1018.79, 1007.40, 1026.19]; DD bei t2 = (1018.79-1007.40)/1018.79
    _assert(abs(s["max_drawdown_pct"] - 1.118) < 0.01, "7. max_drawdown=1.12%")


def test_by_symbol():
    s = compute_stats(_TRADES, start_balance=1000.0)
    btc = s["by_symbol"]["BTC/USDT"]
    eth = s["by_symbol"]["ETH/USDT"]
    _assert(btc["total_trades"] == 2 and btc["wins"] == 2,
            "8. by_symbol BTC: 2 Trades, 2 Gewinner")
    _assert(abs(btc["net_pnl"] - 37.58) < 0.01, "8. by_symbol BTC: net_pnl=37.58")
    _assert(eth["total_trades"] == 1 and eth["losses"] == 1,
            "8. by_symbol ETH: 1 Trade, 1 Verlierer")


def test_by_direction():
    s = compute_stats(_TRADES, start_balance=1000.0)
    _assert(s["by_direction"]["long"]["total_trades"] == 2,
            "9. by_direction long: 2 Trades")
    _assert(s["by_direction"]["short"]["total_trades"] == 1,
            "9. by_direction short: 1 Trade")


def main():
    print("Statistik-Tests\n")
    test_empty()
    test_basic_counts()
    test_pnl_and_fees()
    test_averages_expectancy()
    test_profit_factor()
    test_profit_factor_no_losses()
    test_max_drawdown()
    test_by_symbol()
    test_by_direction()
    print(f"\n{_pass + _fail} Tests: {_pass} bestanden, {_fail} fehlgeschlagen.")
    if _fail > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
