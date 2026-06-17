"""
Multi-Coin / Multi-Config Sweep ueber die MTF-Backtest-Engine.

Faehrt fuer jeden Coin die 8 benannten Configs:
    entry_signal (anchor|divergence) x MFI (aus|an) x RR (2:1|3:1),
    alle mit Fibonacci, Gate 1h+30m.

Effizient: pro Coin werden die HTF-Daten + Divergenz-Zustaende NUR EINMAL
geladen, die 5m-Setups je Entry-Signal einmal. Danach laeuft simulate_mtf()
8x in-memory (kein 56-faches Neuladen).

Aufruf:
    python scripts/sweep_mtf.py --start 2026-03-01 --end 2026-05-31
    python scripts/sweep_mtf.py --coins BTC/USDT GOLD ...   # Teilmenge
"""

import argparse
import csv
import sys
from pathlib import Path

import pandas as pd
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.backtest_mtf import (  # noqa: E402
    _ENTRY_FILTER_TFS,
    _HTF_INTERVALS,
    _INTERVAL_MS,
    _load_and_prepare,
    _load_entry_tf,
    build_divergence_state,
    simulate_mtf,
)
from src.config_utils import resolve_config  # noqa: E402  (nur fuer Vollstaendigkeit)
from src.paper.stats import compute_stats  # noqa: E402

DEFAULT_COINS = ["BTC/USDT", "ETH/USDT", "GOLD", "DOW", "NAS100", "US500", "GER40"]
FIB_SWING_TF = "1h"


def _load_symbol(symbol, start_dt, end_dt, config, data_source):
    """Laedt HTF-Daten + Divergenz-Zustaende (config-unabhaengig) fuer EIN Symbol."""
    load_tfs = list(dict.fromkeys(_HTF_INTERVALS + _ENTRY_FILTER_TFS))
    if FIB_SWING_TF not in load_tfs:
        load_tfs.append(FIB_SWING_TF)

    htf = {}
    for tf in load_tfs:
        df = _load_and_prepare(symbol, tf, start_dt, end_dt, config,
                               verbose=False, data_source=data_source)
        htf[f"df_{tf}"] = df
        htf[f"ims_{tf}"] = _INTERVAL_MS.get(tf, 0)

    for tf in (_HTF_INTERVALS + _ENTRY_FILTER_TFS):
        df = htf[f"df_{tf}"]
        htf[f"state_{tf}"] = build_divergence_state(df, config) if not df.empty else None

    return htf


def main():
    parser = argparse.ArgumentParser(description="MTF Multi-Coin/Config-Sweep")
    parser.add_argument("--coins", nargs="+", default=DEFAULT_COINS)
    parser.add_argument("--start", default="2026-03-01")
    parser.add_argument("--end", default="2026-05-31")
    parser.add_argument("--data-source", default="auto")
    parser.add_argument("--margin-usd", type=float, default=10.0)
    parser.add_argument("--leverage", type=float, default=50.0)
    parser.add_argument("--balance", type=float, default=1000.0)
    parser.add_argument("--out", default="data/sweep_results.csv")
    args = parser.parse_args()

    start_dt = pd.Timestamp(args.start, tz="UTC")
    end_dt = pd.Timestamp(args.end, tz="UTC")
    config = yaml.safe_load((PROJECT_ROOT / "config" / "config.yaml").read_text(encoding="utf-8"))

    print("=" * 78)
    print(f"  SWEEP  {start_dt.date()} -> {end_dt.date()}  |  {len(args.coins)} Coins x 8 Configs")
    print(f"  Einsatz {args.margin_usd:.0f} USD x {args.leverage:.0f}x  |  Quelle: {args.data_source}")
    print("=" * 78)

    rows = []
    for symbol in args.coins:
        print(f"\n  [{symbol}] HTF laden...", flush=True)
        try:
            htf = _load_symbol(symbol, start_dt, end_dt, config, args.data_source)
        except Exception as exc:
            print(f"  [{symbol}] ! HTF-Fehler: {exc}")
            continue
        htf_states = {symbol: htf}

        for entry_signal in ("anchor", "divergence"):
            try:
                df_5m, setups = _load_entry_tf(symbol, start_dt, end_dt, config,
                                               data_source=args.data_source,
                                               entry_signal=entry_signal)
            except Exception as exc:
                print(f"  [{symbol}/{entry_signal}] ! 5m-Fehler: {exc}")
                continue
            if df_5m.empty:
                print(f"  [{symbol}/{entry_signal}] ! keine 5m-Daten")
                continue
            symbol_data = {symbol: (df_5m, setups)}

            for tp_rr in (2, 3):
                for mfi in (False, True):
                    trades, _ = simulate_mtf(
                        symbol_data, htf_states, config,
                        start_balance=args.balance,
                        fixed_margin_usd=args.margin_usd,
                        leverage=args.leverage,
                        use_fibonacci=True,
                        tp_rr=tp_rr,
                        fib_swing_tf=FIB_SWING_TF,
                        mfi_directional=mfi,
                        mfi_long_min=45.0,
                        mfi_short_max=55.0,
                        entry_timing=False,
                    )
                    st = compute_stats(trades, start_balance=args.balance)
                    bd = st.get("by_direction", {})
                    row = {
                        "coin": symbol,
                        "entry": entry_signal,
                        "mfi": "MFI" if mfi else "-",
                        "rr": f"{tp_rr}:1",
                        "trades": st["total_trades"],
                        "wr": round(st["win_rate_pct"], 1),
                        "net_pnl": round(st["net_pnl"], 2),
                        "pf": round(st["profit_factor"], 2) if st["profit_factor"] else None,
                        "max_dd": round(st["max_drawdown_pct"], 1),
                        "long_pnl": round(bd.get("long", {}).get("net_pnl", 0.0), 2),
                        "short_pnl": round(bd.get("short", {}).get("net_pnl", 0.0), 2),
                    }
                    rows.append(row)
                    tag = f"{entry_signal[:4]}/{'MFI' if mfi else '---'}/{tp_rr}:1"
                    print(f"  [{symbol}] {tag:18s} -> {row['trades']:3d} Tr | "
                          f"WR {row['wr']:4.1f}% | {row['net_pnl']:+8.2f} USD | "
                          f"PF {row['pf']}", flush=True)

    # CSV speichern
    if rows:
        out = PROJECT_ROOT / args.out
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print(f"\n  Gespeichert: {out}  ({len(rows)} Zeilen)")

    # Ergebnis-Matrix (sortiert nach net_pnl)
    print("\n" + "=" * 78)
    print("  SWEEP-ERGEBNIS — sortiert nach Netto-PnL")
    print("=" * 78)
    print(f"  {'Coin':10s} {'Entry':5s} {'MFI':4s} {'RR':4s} {'Tr':>4s} "
          f"{'WR%':>5s} {'NetPnL':>9s} {'PF':>5s} {'MaxDD':>6s} {'Long':>8s} {'Short':>8s}")
    print("  " + "-" * 74)
    for r in sorted(rows, key=lambda x: x["net_pnl"], reverse=True):
        pf = f"{r['pf']:.2f}" if r["pf"] is not None else "  -  "
        print(f"  {r['coin']:10s} {r['entry'][:4]:5s} {r['mfi']:4s} {r['rr']:4s} "
              f"{r['trades']:>4d} {r['wr']:>5.1f} {r['net_pnl']:>+9.2f} {pf:>5s} "
              f"{r['max_dd']:>5.1f}% {r['long_pnl']:>+8.2f} {r['short_pnl']:>+8.2f}")
    print("=" * 78)


if __name__ == "__main__":
    main()
