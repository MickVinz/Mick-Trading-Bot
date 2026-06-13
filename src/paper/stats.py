"""
Statistik-Auswertung der abgeschlossenen Paper-Trades (trades.csv).

Reine Funktion ohne Seiteneffekte: nimmt die geparsten Trade-Zeilen (Dicts,
Werte duerfen Strings aus dem CSV sein) und liefert ein Kennzahlen-Dict.

Kennzahlen (gesamt + pro Coin + Long/Short):
  - total_trades, wins, losses, win_rate_pct
  - net_pnl (Summe pnl_usd, NETTO nach Kosten), total_fees, gross_pnl
  - avg_win, avg_loss (Verlust negativ)
  - expectancy (erwarteter PnL pro Trade = net_pnl / total_trades)
  - profit_factor (Summe Gewinne / |Summe Verluste|; None ohne Verlierer)
  - max_drawdown_pct (groesster Peak-to-Trough-Rueckgang der Equity-Kurve)

Trefferdefinition: pnl_usd > 0 = Gewinner, < 0 = Verlierer (0 zaehlt als neutral,
fliesst aber in net_pnl ein).
"""

from __future__ import annotations

from typing import Optional


def _f(row: dict, key: str, default: float = 0.0) -> float:
    """Robustes float()-Parsen eines CSV-Feldes."""
    try:
        return float(row.get(key, default))
    except (TypeError, ValueError):
        return default


def _core_metrics(pnls: list[float], fees: list[float]) -> dict:
    """Kennzahlen, die ohne Equity-Kurve auskommen (auch fuer Untergruppen)."""
    total = len(pnls)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    net = sum(pnls)
    fee_sum = sum(fees)

    gross_wins = sum(wins)
    gross_losses = abs(sum(losses))
    profit_factor: Optional[float] = (
        gross_wins / gross_losses if gross_losses > 0 else None
    )

    return {
        "total_trades": total,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": (len(wins) / total * 100) if total else 0.0,
        "net_pnl": net,
        "total_fees": fee_sum,
        "gross_pnl": net + fee_sum,
        "avg_win": (sum(wins) / len(wins)) if wins else 0.0,
        "avg_loss": (sum(losses) / len(losses)) if losses else 0.0,
        "expectancy": (net / total) if total else 0.0,
        "profit_factor": profit_factor,
    }


def _max_drawdown_pct(equity: list[float]) -> float:
    """Groesster prozentualer Peak-to-Trough-Rueckgang der Equity-Kurve."""
    peak = equity[0] if equity else 0.0
    max_dd = 0.0
    for value in equity:
        if value > peak:
            peak = value
        if peak > 0:
            dd = (peak - value) / peak * 100
            if dd > max_dd:
                max_dd = dd
    return max_dd


def compute_stats(trades: list[dict], start_balance: float = 1000.0) -> dict:
    """
    Berechnet alle Kennzahlen aus der Liste abgeschlossener Trades.

    trades       : Liste von Dicts (Reihenfolge = chronologisch), Felder
                   pnl_usd, fees_usd, balance_after, symbol, direction.
    start_balance: Startkapital fuer die Equity-Kurve / Max-Drawdown.
    """
    pnls = [_f(t, "pnl_usd") for t in trades]
    fees = [_f(t, "fees_usd") for t in trades]

    stats = _core_metrics(pnls, fees)

    # Equity-Kurve: Start + balance_after je Trade (chronologisch).
    equity = [start_balance] + [_f(t, "balance_after", start_balance) for t in trades]
    stats["max_drawdown_pct"] = _max_drawdown_pct(equity)

    # Aufschluesselung pro Coin.
    by_symbol: dict = {}
    symbols = sorted({str(t.get("symbol", "")) for t in trades if t.get("symbol")})
    for sym in symbols:
        grp = [t for t in trades if str(t.get("symbol", "")) == sym]
        by_symbol[sym] = _core_metrics(
            [_f(t, "pnl_usd") for t in grp],
            [_f(t, "fees_usd") for t in grp],
        )
    stats["by_symbol"] = by_symbol

    # Aufschluesselung Long/Short.
    by_direction: dict = {}
    for direction in ("long", "short"):
        grp = [t for t in trades if str(t.get("direction", "")).lower() == direction]
        if grp:
            by_direction[direction] = _core_metrics(
                [_f(t, "pnl_usd") for t in grp],
                [_f(t, "fees_usd") for t in grp],
            )
    stats["by_direction"] = by_direction

    return stats
