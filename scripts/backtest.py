"""
Backtest der Anchor-Trigger-Strategie auf historischen BingX-5m-Daten.

Nutzt alle bestehenden Strategie-Module (wavetrend, mfi, detect_setups,
trade_levels, paper_engine-Logik) unveraendert. Simulation laeuft
Multi-Coin mit geteilter Balance, identisch zur Live-Logik.

Ergebnis: Konsolen-Report + data/backtest_trades.csv

Aufruf:
    python scripts/backtest.py                              # Jan 2026 - heute
    python scripts/backtest.py --start 2026-03-01          # ab 1. Maerz
    python scripts/backtest.py --coins BTC/USDT ETH/USDT   # nur 2 Coins
    python scripts/backtest.py --balance 500               # anderes Startkapital
"""

import argparse
import csv
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd
import yaml

from src.config_utils import get_symbols, resolve_config
from src.exchange.bingx_client import fetch_bingx_klines_range
from src.indicators.mfi import calculate_mfi
from src.indicators.wavetrend import calculate_wavetrend, detect_dots
from src.paper.paper_engine import check_exit, exit_costs, size_qty
from src.paper.position import Position
from src.paper.stats import compute_stats
from src.strategy.setup_detector import detect_setups
from src.strategy.trade_levels import calculate_trade_levels


# ---------------------------------------------------------------------------
# Indikatoren + Setups
# ---------------------------------------------------------------------------

def prepare_symbol(df: pd.DataFrame, sym_cfg: dict) -> tuple:
    """Berechnet WaveTrend, MFI, Dots, Setups, Levels fuer ein Symbol."""
    wt = sym_cfg["wavetrend"]
    df = calculate_wavetrend(
        df, n1=wt["n1"], n2=wt["n2"], wt2_sma_length=wt["wt2_sma_length"]
    )
    df = detect_dots(df)
    df = calculate_mfi(df, period=sym_cfg["mfi"]["period"])
    setups = detect_setups(df, sym_cfg)
    setups = calculate_trade_levels(df, setups, sym_cfg)
    return df, setups


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------

def _daily_rollover(state: dict, ts: pd.Timestamp) -> None:
    day = ts.strftime("%Y-%m-%d")
    if state.get("day") != day:
        state["day"] = day
        state["day_start_balance"] = state["balance"]
        state["realized_pnl_today"] = 0.0


def _gate_open(state: dict, pt_cfg: dict) -> bool:
    max_loss = float(pt_cfg.get("max_daily_loss_pct", 10.0))
    day_start = state.get("day_start_balance", state["balance"])
    pnl_today = state.get("realized_pnl_today", 0.0)
    if day_start <= 0:
        return True
    return (pnl_today / day_start * 100) > -max_loss


def _book_notional(positions: dict) -> float:
    return sum(p.qty * p.entry for p in positions.values() if p is not None)


def simulate(symbol_data: dict, config: dict, start_balance: float = 1000.0) -> list:
    """
    Simuliert die Multi-Coin-Strategie auf vorberechneten historischen Daten.

    symbol_data : {symbol: (df, setups)} — vorberechnete Indikatoren + Setups.
    Gibt chronologische Liste von Trade-Dicts zurueck (= trades.csv-Schema).
    """
    pt_cfg = config.get("paper_trading", {})
    max_book_x = float(pt_cfg.get("max_book_notional_x", 0.0))
    sym_cfgs = {sym: resolve_config(config, sym) for sym in symbol_data}

    state = {
        "balance": start_balance,
        "day": "",
        "day_start_balance": start_balance,
        "realized_pnl_today": 0.0,
    }
    positions: dict = {}
    trades: list = []

    # Einheitlicher Zeitstrahl aller Symbols (5m-Kerzen, fast deckungsgleich)
    all_ts = sorted({
        ts
        for df, _ in symbol_data.values()
        for ts in df["timestamp"]
    })

    # Schneller Index-Zugriff: symbol -> DataFrame mit timestamp als Index
    sym_idx = {sym: df.set_index("timestamp") for sym, (df, _) in symbol_data.items()}
    sym_setups = {sym: setups for sym, (_, setups) in symbol_data.items()}

    for ts in all_ts:
        _daily_rollover(state, ts)

        # PHASE 1 — EXITS (alle offenen Positionen pruefen)
        for symbol, position in list(positions.items()):
            if position is None:
                continue
            if ts not in sym_idx[symbol].index:
                continue
            candle = sym_idx[symbol].loc[ts]

            reason, exit_price = check_exit(position, candle)
            if reason is None:
                continue

            gross_pnl = position.pnl(exit_price)
            fees = exit_costs(position, exit_price, reason, pt_cfg)
            pnl_usd = gross_pnl - fees
            balance_before = state["balance"]
            state["balance"] += pnl_usd
            state["realized_pnl_today"] = state.get("realized_pnl_today", 0.0) + pnl_usd

            risk_usd = position.qty * abs(position.entry - position.sl)
            pnl_pct = (pnl_usd / balance_before * 100) if balance_before != 0 else 0.0
            rr = (pnl_usd / risk_usd) if risk_usd > 0 else 0.0
            risk_pct = abs(position.entry - position.sl) / position.entry * 100

            trades.append({
                "symbol": symbol,
                "entry_time": position.entry_time.strftime("%Y-%m-%d %H:%M"),
                "exit_time": ts.strftime("%Y-%m-%d %H:%M"),
                "direction": position.direction,
                "entry": round(position.entry, 4),
                "sl": round(position.sl, 4),
                "tp1": round(position.tp1, 4),
                "exit_price": round(exit_price, 4),
                "exit_reason": reason,
                "qty": round(position.qty, 6),
                "risk_pct": round(risk_pct, 3),
                "rr": round(rr, 3),
                "pnl_usd": round(pnl_usd, 4),
                "pnl_pct": round(pnl_pct, 4),
                "fees_usd": round(fees, 4),
                "balance_after": round(state["balance"], 4),
                "divergence": position.divergence,
            })
            positions[symbol] = None

        # PHASE 2 — ENTRIES (nur wenn Tageslimit offen)
        if not _gate_open(state, pt_cfg):
            continue

        for symbol in symbol_data:
            if positions.get(symbol) is not None:
                continue
            if ts not in sym_idx[symbol].index:
                continue

            setups = sym_setups[symbol]
            if setups is None or setups.empty:
                continue

            mask = (
                (setups["setup_valid"] == True)   # noqa: E712
                & (setups["time"] == ts)
                & (setups["tp1"].notna())
            )
            if "sl_zu_eng" in setups.columns:
                mask &= setups["sl_zu_eng"] == False   # noqa: E712
            if "warmup_artefact" in setups.columns:
                mask &= setups["warmup_artefact"] == False   # noqa: E712
            valid = setups[mask]
            if valid.empty:
                continue

            s = valid.iloc[-1]
            entry = float(s["entry"])
            sl = float(s["sl"])
            tp1 = float(s["tp1"])
            qty = size_qty(state["balance"], entry, sl, pt_cfg)
            if qty <= 0:
                continue

            if max_book_x > 0:
                if _book_notional(positions) + qty * entry > state["balance"] * max_book_x:
                    continue

            positions[symbol] = Position(
                entry=entry, sl=sl, tp1=tp1, qty=qty,
                direction=s["direction"], entry_time=ts,
                divergence=bool(s.get("divergence_active", False)),
            )

    return trades


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

_FIELDNAMES = [
    "symbol", "entry_time", "exit_time", "direction",
    "entry", "sl", "tp1", "exit_price", "exit_reason",
    "qty", "risk_pct", "rr", "pnl_usd", "pnl_pct", "fees_usd",
    "balance_after", "divergence",
]


def _print_stats(stats: dict, start_balance: float) -> None:
    print("\n" + "=" * 60)
    print("  BACKTEST-ERGEBNIS")
    print("=" * 60)
    n = stats["total_trades"]
    wr = stats["win_rate_pct"]
    pnl = stats["net_pnl"]
    fees = stats["total_fees"]
    dd = stats["max_drawdown_pct"]
    exp = stats["expectancy"]
    pf = stats["profit_factor"]
    final = start_balance + pnl
    ret_pct = (final / start_balance - 1) * 100

    print(f"  Trades:        {n}  ({stats['wins']} Gewinner / {stats['losses']} Verlierer)")
    print(f"  Trefferquote:  {wr:.1f} %")
    print(f"  Netto-PnL:     {pnl:+.2f} USDT")
    print(f"  Gebuehren:     {fees:.2f} USDT  (Brutto {pnl + fees:+.2f})")
    print(f"  Profit-Factor: {pf:.2f}" if pf else "  Profit-Factor: n/a (keine Gewinne)")
    print(f"  Expectancy:    {exp:+.2f} USDT / Trade")
    print(f"  Max-Drawdown:  {dd:.1f} %")
    print(f"  Endkapital:    {final:.2f} USDT  ({ret_pct:+.1f} %)")

    print("\n  PRO COIN:")
    for sym, s in stats.get("by_symbol", {}).items():
        wr_s = s["win_rate_pct"]
        pnl_s = s["net_pnl"]
        n_s = s["total_trades"]
        print(f"  {sym:12s}: {n_s:4d} Trades | WR {wr_s:.0f}% | PnL {pnl_s:+.2f} USDT")

    print("\n  LONG / SHORT:")
    for direction, s in stats.get("by_direction", {}).items():
        wr_d = s["win_rate_pct"]
        pnl_d = s["net_pnl"]
        n_d = s["total_trades"]
        print(f"  {direction.upper():5s}: {n_d:4d} Trades | WR {wr_d:.0f}% | PnL {pnl_d:+.2f} USDT")

    print("=" * 60)


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest: Anchor-Trigger-Scalp")
    parser.add_argument("--start", default="2026-01-01",
                        help="Startdatum YYYY-MM-DD (Standard: 2026-01-01)")
    parser.add_argument("--end", default=None,
                        help="Enddatum YYYY-MM-DD (Standard: heute)")
    parser.add_argument("--balance", type=float, default=1000.0,
                        help="Startkapital in USDT (Standard: 1000)")
    parser.add_argument("--coins", nargs="+", default=None,
                        help="Subset der Coins (z.B. BTC/USDT ETH/USDT)")
    parser.add_argument("--no-save", action="store_true",
                        help="backtest_trades.csv NICHT schreiben")
    args = parser.parse_args()

    with open(PROJECT_ROOT / "config" / "config.yaml", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    start_dt = pd.Timestamp(args.start, tz="utc")
    end_raw = args.end or pd.Timestamp.now(tz="utc").strftime("%Y-%m-%d")
    end_dt = pd.Timestamp(end_raw, tz="utc")
    symbols = args.coins or get_symbols(config)
    interval = config.get("market", {}).get("timeframe", "5m")

    candles_est = int((end_dt - start_dt).total_seconds() / 300)
    days = (end_dt - start_dt).days

    print("=" * 60)
    print("  Mick Trading Bot — Backtest (Paper-Simulation)")
    print("=" * 60)
    print(f"  Zeitraum:      {start_dt.date()} -> {end_dt.date()}  ({days} Tage)")
    print(f"  Coins:         {', '.join(symbols)}")
    print(f"  Startkapital:  {args.balance:.0f} USDT")
    print(f"  ~Kerzen/Coin:  {candles_est:,}  (~{candles_est // 1000 + 1} API-Seiten)")
    print("=" * 60)

    # 1. Daten laden + Indikatoren berechnen
    symbol_data = {}
    for symbol in symbols:
        bingx_sym = symbol.replace("/", "-")
        print(f"\n  [{symbol}] Lade historische Daten...", flush=True)
        try:
            df = fetch_bingx_klines_range(
                bingx_sym, interval, start_dt, end_dt, verbose=True
            )
            if df.empty:
                print(f"  [{symbol}] ⚠ Keine Daten — uebersprungen")
                continue
            print(f"  [{symbol}] {len(df):,} Kerzen geladen", flush=True)

            sym_cfg = resolve_config(config, symbol)
            print(f"  [{symbol}] Indikatoren + Setups berechnen...", flush=True)
            df, setups = prepare_symbol(df, sym_cfg)

            n_valid = (setups["setup_valid"] == True).sum() if not setups.empty else 0
            print(f"  [{symbol}] {n_valid} gueltiger Setups", flush=True)
            symbol_data[symbol] = (df, setups)
        except Exception as exc:
            print(f"  [{symbol}] ⚠ Fehler: {exc}")

    if not symbol_data:
        print("\n  Keine Daten geladen. Abbruch.")
        return

    # 2. Simulation
    print(f"\n  Starte Simulation ({len(symbol_data)} Coins)...", flush=True)
    trades = simulate(symbol_data, config, start_balance=args.balance)
    print(f"  Simulation fertig: {len(trades)} abgeschlossene Trades", flush=True)

    # 3. Speichern
    if not args.no_save and trades:
        out_path = PROJECT_ROOT / "data" / "backtest_trades.csv"
        with out_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=_FIELDNAMES)
            writer.writeheader()
            writer.writerows(trades)
        print(f"\n  Gespeichert: {out_path}")
    elif not trades:
        print("\n  Keine Trades — nichts gespeichert.")

    # 4. Stats
    stats = compute_stats(trades, start_balance=args.balance)
    _print_stats(stats, args.balance)


if __name__ == "__main__":
    main()
