"""
Statistik-Report der abgeschlossenen Paper-Trades (data/trades.csv).

Aufruf (aus dem Projektordner):
    python scripts/show_stats.py

Liest trades.csv + Startkapital aus config.yaml und druckt einen
Kennzahlen-Report: Trefferquote, Netto-PnL, Gebuehren, Expectancy,
Profit-Factor, Max-Drawdown — gesamt, pro Coin und Long/Short.
KEINE echten Orders, nur Auswertung.
"""
import csv
import sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import yaml

from src.paper.stats import compute_stats

CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"
TRADES_PATH = PROJECT_ROOT / "data" / "trades.csv"


def _load_trades(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _pf(value) -> str:
    return "∞ (keine Verlierer)" if value is None else f"{value:.2f}"


def _block(title: str, s: dict, indent: str = "") -> None:
    print(f"{indent}{title}")
    print(f"{indent}  Trades:        {s['total_trades']}  "
          f"({s['wins']} Gewinner / {s['losses']} Verlierer)")
    print(f"{indent}  Trefferquote:  {s['win_rate_pct']:.1f} %")
    print(f"{indent}  Netto-PnL:     {s['net_pnl']:+.2f} USDT")
    print(f"{indent}  Gebuehren:     {s['total_fees']:.2f} USDT  "
          f"(Brutto waere {s['gross_pnl']:+.2f})")
    print(f"{indent}  Avg-Win:       {s['avg_win']:+.2f}  |  "
          f"Avg-Loss: {s['avg_loss']:+.2f}")
    print(f"{indent}  Expectancy:    {s['expectancy']:+.2f} USDT / Trade")
    print(f"{indent}  Profit-Factor: {_pf(s['profit_factor'])}")


def main() -> None:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        config = yaml.safe_load(f)
    start_balance = float(config.get("paper_trading", {}).get("start_balance", 1000.0))

    trades = _load_trades(TRADES_PATH)
    s = compute_stats(trades, start_balance=start_balance)

    print("=" * 60)
    print("  Paper-Trading-Statistik  (reine Auswertung, keine Orders)")
    print("=" * 60)

    if s["total_trades"] == 0:
        print("  Noch keine abgeschlossenen Trades in trades.csv.")
        return

    _block("GESAMT", s)
    print(f"  Max-Drawdown:  {s['max_drawdown_pct']:.2f} %")
    print()

    print("PRO COIN")
    for sym, sub in s["by_symbol"].items():
        print()
        _block(sym, sub, indent="  ")
    print()

    print("LONG / SHORT")
    for direction, sub in s["by_direction"].items():
        print()
        _block(direction.upper(), sub, indent="  ")

    print("\n" + "=" * 60)
    # Ehrliche Einordnung: Break-even-Trefferquote bei aktuellem Avg-RR.
    if s["avg_loss"] != 0 and s["wins"] > 0:
        rr_eff = abs(s["avg_win"] / s["avg_loss"])
        be_wr = 1 / (1 + rr_eff) * 100
        print(f"  Effektives RR (Avg-Win/Avg-Loss): {rr_eff:.2f} : 1")
        print(f"  Break-even-Trefferquote:          {be_wr:.1f} %  "
              f"(aktuell: {s['win_rate_pct']:.1f} %)")
    print("=" * 60)


if __name__ == "__main__":
    main()
