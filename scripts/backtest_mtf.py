"""
Backtest: Multi-Timeframe Divergenz-Kaskade (4H + 1H + 30m Gate).

Gate-Logik:
    4H, 1H und 30m muessen ALLE eine aktive Divergenz in DERSELBEN Richtung
    zeigen — andernfalls kein Trade. Die aktive Richtung wird auf 5m-Ebene
    fuer den Entry benutzt.

Exit-Struktur (3:1 mit Teilschutz):
    TP1 (2:1 RR): 50 % schliessen, SL auf Breakeven ziehen.
    TP2 (3:1 RR): restliche 50 % schliessen.
    SL jederzeit: gesamte verbleibende Position.

Divergenz-Zustand-Logik:
    Aktiv ab: erster Kerze, an der detect_divergence() True liefert.
    Erloescht: wenn wt1 die Nulllinie in der Gegenrichtung kreuzt
               (Bullisch: wt1 > 0 invalidiert, Bearisch: wt1 < 0 invalidiert).

Aufruf:
    python scripts/backtest_mtf.py                   # Jan 2026 - heute, alle Coins
    python scripts/backtest_mtf.py --coins BTC/USDT  # nur BTC
    python scripts/backtest_mtf.py --start 2026-03-01
"""

import argparse
import csv
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd
import yaml

from src.config_utils import get_symbols, resolve_config
from src.exchange.bingx_client import fetch_bingx_klines_range
from src.indicators.mfi import calculate_mfi
from src.indicators.wavetrend import calculate_wavetrend, detect_dots
from src.paper.paper_engine import exit_costs, size_qty
from src.paper.stats import compute_stats
from src.strategy.divergence_detector import detect_divergence
from src.strategy.setup_detector import detect_setups
from src.strategy.trade_levels import calculate_trade_levels

# Gate-Timeframes: beide muessen aktive Divergenz in gleicher Richtung zeigen
_HTF_INTERVALS = ["1h", "30m"]

# Entry-Kaskade: beide muessen zusaetzlich zur Gate-Richtung passen
_ENTRY_FILTER_TFS = ["15m", "1m"]

# Entry-Timeframe (Anchor-Trigger Setup)
_ENTRY_INTERVAL = "5m"

# Intervall-Dauer in ms — fuer close_time-Berechnung (Repaint-Schutz)
_INTERVAL_MS: dict = {
    "4h": 14_400_000, "1h": 3_600_000, "30m": 1_800_000,
    "15m": 900_000, "5m": 300_000, "1m": 60_000,
}

# Fibonacci-Konfiguration (aktive Levels: 61.8% + 78.6%)
_FIB_LEVELS = [0.618, 0.786]
_FIB_LOOKBACK = 50       # 1H-Kerzen fuer Swing-Erkennung
_FIB_TOLERANCE_PCT = 0.5 # +/- 0.5% Toleranzband um das Level


# ---------------------------------------------------------------------------
# Daten + Indikatoren
# ---------------------------------------------------------------------------

def _load_and_prepare(symbol: str, interval: str, start_dt, end_dt, config: dict,
                      verbose: bool = False):
    """Laedt und bereitet einen einzelnen Timeframe vor (WaveTrend + MFI)."""
    bingx_sym = symbol.replace("/", "-")
    sym_cfg = resolve_config(config, symbol)
    wt = sym_cfg["wavetrend"]

    df = fetch_bingx_klines_range(bingx_sym, interval, start_dt, end_dt, verbose=verbose)
    if df.empty:
        return df

    df = calculate_wavetrend(df, n1=wt["n1"], n2=wt["n2"],
                             wt2_sma_length=wt["wt2_sma_length"])
    df = detect_dots(df)
    df = calculate_mfi(df, period=sym_cfg["mfi"]["period"])
    return df


def _load_entry_tf(symbol: str, start_dt, end_dt, config: dict):
    """Laedt 5m-Daten inkl. Setups und Trade-Levels fuer die Entry-Entscheidung."""
    bingx_sym = symbol.replace("/", "-")
    sym_cfg = resolve_config(config, symbol)
    wt = sym_cfg["wavetrend"]

    df = fetch_bingx_klines_range(
        bingx_sym, _ENTRY_INTERVAL, start_dt, end_dt, verbose=True
    )
    if df.empty:
        return df, pd.DataFrame()

    df = calculate_wavetrend(df, n1=wt["n1"], n2=wt["n2"],
                             wt2_sma_length=wt["wt2_sma_length"])
    df = detect_dots(df)
    df = calculate_mfi(df, period=sym_cfg["mfi"]["period"])
    setups = detect_setups(df, sym_cfg)
    setups = calculate_trade_levels(df, setups, sym_cfg)
    return df, setups


# ---------------------------------------------------------------------------
# Divergenz-Zustand vorberechnen
# ---------------------------------------------------------------------------

def build_divergence_state(df: pd.DataFrame, config: dict) -> pd.Series:
    """
    Berechnet fuer jeden Kerzen-Index den aktiven Divergenz-Zustand.

    Gibt eine Series (Index = df.index) zurueck mit Werten:
        'long'  = bullische Divergenz aktiv
        'short' = bearische Divergenz aktiv
        None    = keine aktive Divergenz

    Aktivierung: wenn detect_divergence() True liefert.
    Invalidierung: wenn wt1 die Nulllinie in der Gegenrichtung kreuzt
        (Long: wt1 > 0 loescht bullische Div.; Short: wt1 < 0 loescht bearische).
    """
    if df.empty or "wt1" not in df.columns:
        return pd.Series([None] * len(df), index=df.index)

    n = len(df)
    states = [None] * n
    active: Optional[str] = None

    # Genuegend Aufwaerm-Kerzen fuer Pivot-Berechnung benoetigt
    div_cfg = config.get("divergence", {})
    start_i = int(div_cfg.get("divergence_window", 20)) + int(div_cfg.get("pivot_lookback", 3)) + 5

    for i in range(start_i, n):
        wt1 = float(df["wt1"].iloc[i])

        # Nulllinien-Kreuzungs-Invalidierung (Gegenrichtung)
        if active == "long" and wt1 > 0:
            active = None
        elif active == "short" and wt1 < 0:
            active = None

        # Neue Divergenz pruefen (kann bestehende ueberschreiben)
        for direction in ("long", "short"):
            result = detect_divergence(df, i, direction, config)
            if result["divergence_active"]:
                active = direction
                break

        states[i] = active

    return pd.Series(states, index=df.index)


def _state_at_time(state_series: pd.Series, df_htf: pd.DataFrame,
                   query_time: pd.Timestamp, interval_ms: int) -> Optional[str]:
    """
    Gibt den Divergenz-Zustand des HTF zum Zeitpunkt query_time zurueck.

    Benutzt nur Kerzen, deren close_time <= query_time (Repaint-Schutz).
    close_time = open_time (timestamp) + interval_ms.
    """
    close_times = df_htf["timestamp"] + pd.Timedelta(milliseconds=interval_ms)
    mask = close_times <= query_time
    if not mask.any():
        return None
    last_idx = df_htf.index[mask][-1]
    return state_series.iloc[last_idx]


# ---------------------------------------------------------------------------
# Positions-Modell (partial exit)
# ---------------------------------------------------------------------------

@dataclass
class MTFPosition:
    """Position mit Teilausstieg-Unterstuetzung (50%@TP1, SL->BE, 50%@TP2)."""
    entry: float
    sl: float
    tp1: float
    tp2: float
    qty_total: float
    qty_remaining: float
    direction: str
    entry_time: pd.Timestamp
    divergence: bool
    tp1_hit: bool = False

    @classmethod
    def open(cls, entry, sl, tp1, tp2, qty, direction, entry_time, divergence):
        return cls(
            entry=entry, sl=sl, tp1=tp1, tp2=tp2,
            qty_total=qty, qty_remaining=qty,
            direction=direction, entry_time=entry_time,
            divergence=divergence, tp1_hit=False,
        )

    def check_exit(self, candle: pd.Series):
        """
        Gibt (reason, exit_price, qty_closed) oder (None, None, 0) zurueck.

        Phasen:
          1. Noch kein TP1: pruefen SL und TP1.
             SL schlaegt TP1 (konservativ).
          2. TP1 bereits hit (SL jetzt auf BE):
             pruefen SL (=entry) und TP2.
        """
        if self.direction == "long":
            sl_hit = candle["low"] <= self.sl
            if not self.tp1_hit:
                tp1_hit = candle["high"] >= self.tp1
                if sl_hit:
                    return "sl", self.sl, self.qty_remaining
                if tp1_hit:
                    return "tp1", self.tp1, self.qty_remaining * 0.5
                return None, None, 0
            else:
                tp2_hit = candle["high"] >= self.tp2
                if sl_hit:
                    return "sl_be", self.sl, self.qty_remaining
                if tp2_hit:
                    return "tp2", self.tp2, self.qty_remaining
                return None, None, 0
        else:  # short
            sl_hit = candle["high"] >= self.sl
            if not self.tp1_hit:
                tp1_hit = candle["low"] <= self.tp1
                if sl_hit:
                    return "sl", self.sl, self.qty_remaining
                if tp1_hit:
                    return "tp1", self.tp1, self.qty_remaining * 0.5
                return None, None, 0
            else:
                tp2_hit = candle["low"] <= self.tp2
                if sl_hit:
                    return "sl_be", self.sl, self.qty_remaining
                if tp2_hit:
                    return "tp2", self.tp2, self.qty_remaining
                return None, None, 0


def _compute_tp2(entry: float, sl: float, direction: str) -> float:
    """Berechnet TP2 bei 3:1 RR."""
    risk = abs(entry - sl)
    if direction == "long":
        return entry + 3.0 * risk
    return entry - 3.0 * risk


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
    pnl = state.get("realized_pnl_today", 0.0)
    return (pnl / day_start * 100 > -max_loss) if day_start > 0 else True


def _book_notional(positions: dict) -> float:
    return sum(p.qty_remaining * p.entry for p in positions.values() if p)


def _precompute_lookup(symbol_data: dict, htf_states: dict, tfs: list) -> dict:
    """
    Baut pro Symbol + TF eine 5m-Timestamp-indexierte Divergenz-State-Series vor.

    O(n_htf + n_5m) via reindex+ffill — danach O(1) Dict-Lookup in der Sim-Schleife.
    Wird sowohl fuer Gate-TFs (1H+30m) als auch Entry-Filter-TFs (15m+1m) genutzt.

    Rueckgabe: {symbol: {tf: pd.Series(index=5m-ts, values=direction|None)}}
    """
    lookup: dict = {}
    for symbol, (df_5m, _) in symbol_data.items():
        ts_5m = pd.DatetimeIndex(df_5m["timestamp"])
        htf = htf_states.get(symbol, {})
        lookup[symbol] = {}

        for tf in tfs:
            state_s = htf.get(f"state_{tf}")
            df_htf = htf.get(f"df_{tf}")
            ims = htf.get(f"ims_{tf}", 0)

            if state_s is None or df_htf is None or df_htf.empty:
                lookup[symbol][tf] = pd.Series(
                    [None] * len(ts_5m), index=ts_5m, dtype=object
                )
                continue

            # close_time = open_time + interval_ms (Repaint-Schutz)
            close_times = pd.DatetimeIndex(
                df_htf["timestamp"] + pd.Timedelta(milliseconds=ims)
            )
            htf_state = pd.Series(state_s.values, index=close_times, dtype=object)
            htf_state = htf_state[~htf_state.index.duplicated(keep="last")]
            htf_state = htf_state.sort_index()

            # Vereinige HTF-close-times + 5m-Timestamps, ffill, dann 5m-Slice
            combined_idx = htf_state.index.union(ts_5m)
            combined = htf_state.reindex(combined_idx).ffill()
            lookup[symbol][tf] = combined.reindex(ts_5m)

    return lookup


def _precompute_fib_lookup(symbol_data: dict, htf_states: dict) -> dict:
    """
    Berechnet pro Symbol + 5m-Timestamp ob der 5m-Schlusskurs nahe an einem
    Fibonacci-Level liegt (61.8% oder 78.6% des letzten 1H-Swings).

    Long:  Preis nahe Retracement-Support (swing_high - fib * range)
    Short: Preis nahe Retracement-Resistance (swing_low + fib * range)

    Rueckgabe: {symbol: {"fib_near_long": Series(bool), "fib_near_short": Series(bool)}}
    """
    tol = _FIB_TOLERANCE_PCT / 100.0
    fib_lookup: dict = {}

    for symbol, (df_5m, _) in symbol_data.items():
        ts_5m = pd.DatetimeIndex(df_5m["timestamp"])
        df_1h = htf_states.get(symbol, {}).get("df_1h")

        empty = {
            "fib_near_long": pd.Series([False] * len(ts_5m), index=ts_5m),
            "fib_near_short": pd.Series([False] * len(ts_5m), index=ts_5m),
        }
        if df_1h is None or df_1h.empty:
            fib_lookup[symbol] = empty
            continue

        # Rolling Swing High/Low auf 1H
        df_h = df_1h.copy()
        df_h["sw_hi"] = df_h["high"].rolling(_FIB_LOOKBACK, min_periods=_FIB_LOOKBACK).max()
        df_h["sw_lo"] = df_h["low"].rolling(_FIB_LOOKBACK, min_periods=_FIB_LOOKBACK).min()
        rng = df_h["sw_hi"] - df_h["sw_lo"]

        for lvl in _FIB_LEVELS:
            key = int(lvl * 1000)
            df_h[f"dn_{key}"] = df_h["sw_hi"] - lvl * rng   # Long-Support
            df_h[f"up_{key}"] = df_h["sw_lo"] + lvl * rng   # Short-Resistance

        # Repaint-Schutz: nur abgeschlossene 1H-Kerzen verwenden
        ims_1h = _INTERVAL_MS["1h"]
        close_times = pd.DatetimeIndex(df_h["timestamp"] + pd.Timedelta(milliseconds=ims_1h))
        fib_cols = [c for c in df_h.columns if c.startswith(("dn_", "up_"))]
        df_h_idx = df_h[fib_cols].copy()
        df_h_idx.index = close_times
        df_h_idx = df_h_idx[~df_h_idx.index.duplicated(keep="last")].sort_index()

        combined_idx = df_h_idx.index.union(ts_5m)
        fib_at_5m = df_h_idx.reindex(combined_idx).ffill().reindex(ts_5m)

        close_5m = df_5m.set_index("timestamp")["close"].reindex(ts_5m)

        dn_cols = [c for c in fib_cols if c.startswith("dn_")]
        up_cols = [c for c in fib_cols if c.startswith("up_")]

        near_long = pd.Series(False, index=ts_5m)
        for col in dn_cols:
            lvl_p = fib_at_5m[col]
            near_long |= (abs(close_5m - lvl_p) / lvl_p.replace(0, float("nan")) <= tol)

        near_short = pd.Series(False, index=ts_5m)
        for col in up_cols:
            lvl_p = fib_at_5m[col]
            near_short |= (abs(close_5m - lvl_p) / lvl_p.replace(0, float("nan")) <= tol)

        fib_lookup[symbol] = {
            "fib_near_long": near_long.fillna(False),
            "fib_near_short": near_short.fillna(False),
        }

    return fib_lookup


def simulate_mtf(
    symbol_data: dict,
    htf_states: dict,
    config: dict,
    start_balance: float = 1000.0,
    fixed_margin_usd: float = 0.0,
    leverage: float = 1.0,
    use_fibonacci: bool = False,
) -> list:
    """
    Multi-Coin-Simulation mit MTF-Gate (1H+30m) + Entry-Kaskade (15m+1m).

    Gate:    1H + 30m muessen BEIDE aktive Divergenz in gleicher Richtung zeigen.
    Kaskade: 15m + 1m muessen ebenfalls uebereinstimmen (Entry-Praezision).
    Signal:  5m Anchor-Trigger Setup (detect_setups).
    Fibonacci (optional): Entry-Preis muss nahe 61.8%/78.6%-Level des 1H-Swings sein.

    fixed_margin_usd > 0: fixer Einsatz pro Trade, Hebel separat (1:1 RR).
    fixed_margin_usd = 0: risk_pct-basiertes Sizing aus Config.
    """
    pt_cfg = config.get("paper_trading", {})
    max_open = 4
    max_book_x = float(pt_cfg.get("max_book_notional_x", 0.0))

    # O(n_htf + n_5m) Vorberechnung — danach O(1) per Kerze
    gate_lookup = _precompute_lookup(symbol_data, htf_states, _HTF_INTERVALS)
    entry_filter_lookup = _precompute_lookup(symbol_data, htf_states, _ENTRY_FILTER_TFS)
    fib_lookup = _precompute_fib_lookup(symbol_data, htf_states) if use_fibonacci else {}

    state = {
        "balance": start_balance,
        "day": "",
        "day_start_balance": start_balance,
        "realized_pnl_today": 0.0,
    }
    positions: dict = {}
    trades: list = []

    all_ts = sorted({
        ts
        for df5m, _ in symbol_data.values()
        for ts in df5m["timestamp"]
    })

    sym_idx = {sym: df.set_index("timestamp") for sym, (df, _) in symbol_data.items()}
    sym_setups = {sym: setups for sym, (_, setups) in symbol_data.items()}

    # Kosten-Parameter aus Config (Prozent → Dezimal)
    taker = float(pt_cfg.get("taker_fee_pct", 0.05)) / 100.0
    slip = float(pt_cfg.get("slippage_pct", 0.02)) / 100.0

    for ts in all_ts:
        _daily_rollover(state, ts)

        # PHASE 1 — EXITS (inkl. Teilausstieg)
        for symbol in list(positions):
            pos = positions.get(symbol)
            if pos is None:
                continue
            if ts not in sym_idx[symbol].index:
                continue

            candle = sym_idx[symbol].loc[ts]
            reason, exit_price, qty_closed = pos.check_exit(candle)
            if reason is None:
                continue

            # Gebühren: Entry-Kosten einmalig beim ersten Exit (Round-Trip-Modell).
            # TP-Exits: Limit-Order (kein Slip). SL-Exits: Stop-Market (+ Slip).
            if not pos.tp1_hit:
                # Erster Exit: Entry-Fee auf volle Position + Exit-Fee auf qty_closed
                entry_cost = pos.qty_total * pos.entry * (taker + slip)
                if reason.startswith("sl"):
                    exit_cost = qty_closed * exit_price * (taker + slip)
                else:
                    exit_cost = qty_closed * exit_price * taker
                fees = entry_cost + exit_cost
            else:
                # Zweiter Exit: Entry-Fee bereits verrechnet — nur Exit-Fee
                if reason.startswith("sl"):
                    fees = qty_closed * exit_price * (taker + slip)
                else:
                    fees = qty_closed * exit_price * taker

            gross = qty_closed * (exit_price - pos.entry) * (1 if pos.direction == "long" else -1)
            pnl = gross - fees
            bal_before = state["balance"]
            state["balance"] += pnl
            state["realized_pnl_today"] = state.get("realized_pnl_today", 0.0) + pnl

            trade_row = {
                "symbol": symbol,
                "entry_time": pos.entry_time.strftime("%Y-%m-%d %H:%M"),
                "exit_time": ts.strftime("%Y-%m-%d %H:%M"),
                "direction": pos.direction,
                "entry": round(pos.entry, 4),
                "sl": round(pos.sl, 4),
                "tp1": round(pos.tp1, 4),
                "exit_price": round(exit_price, 4),
                "exit_reason": reason,
                "qty": round(qty_closed, 6),
                "risk_pct": round(abs(pos.entry - pos.sl) / pos.entry * 100, 3),
                "rr": round(pnl / (pos.qty_total * abs(pos.entry - pos.sl)), 3)
                      if pos.qty_total * abs(pos.entry - pos.sl) > 0 else 0.0,
                "pnl_usd": round(pnl, 4),
                "pnl_pct": round(pnl / bal_before * 100 if bal_before else 0, 4),
                "fees_usd": round(fees, 4),
                "balance_after": round(state["balance"], 4),
                "divergence": pos.divergence,
            }
            trades.append(trade_row)

            if reason == "tp1":
                # Teilausstieg: SL auf Breakeven, Position weiter offen
                pos.qty_remaining -= qty_closed
                pos.sl = pos.entry
                pos.tp1_hit = True
            else:
                # Vollstaendiger Exit
                positions[symbol] = None

        # PHASE 2 — ENTRIES
        if not _gate_open(state, pt_cfg):
            continue

        # Max-4-Limit pruefen
        open_count = sum(1 for p in positions.values() if p is not None)
        if open_count >= max_open:
            continue

        # MTF-Gate pro Coin pruefen + Setups sammeln
        candidates = []
        for symbol in symbol_data:
            if positions.get(symbol) is not None:
                continue
            if ts not in sym_idx[symbol].index:
                continue

            # Gate: 1H + 30m — BEIDE muessen aktive Divergenz in gleicher Richtung zeigen
            sym_gate = gate_lookup.get(symbol, {})
            gate_dirs = [sym_gate.get(tf, pd.Series(dtype=object)).get(ts) for tf in _HTF_INTERVALS]
            if None in gate_dirs or len(set(gate_dirs)) != 1:
                continue
            gate_direction = gate_dirs[0]
            if gate_direction is None:
                continue

            # Entry-Kaskade: 15m + 1m muessen Gate-Richtung bestaetigen
            sym_entry = entry_filter_lookup.get(symbol, {})
            entry_dirs = [sym_entry.get(tf, pd.Series(dtype=object)).get(ts) for tf in _ENTRY_FILTER_TFS]
            if None in entry_dirs or any(d != gate_direction for d in entry_dirs):
                continue

            # 5m-Setup in Gate-Richtung suchen
            setups = sym_setups.get(symbol, pd.DataFrame())
            if setups is None or setups.empty:
                continue

            mask = (
                (setups["setup_valid"] == True)    # noqa: E712
                & (setups["time"] == ts)
                & (setups["tp1"].notna())
                & (setups["direction"] == gate_direction)
            )
            if "sl_zu_eng" in setups.columns:
                mask &= setups["sl_zu_eng"] == False   # noqa: E712
            if "warmup_artefact" in setups.columns:
                mask &= setups["warmup_artefact"] == False  # noqa: E712
            valid = setups[mask]
            if valid.empty:
                continue

            s = valid.iloc[-1]

            # Fibonacci-Filter (optional): Entry nur an 61.8%/78.6%-Level
            if use_fibonacci:
                sym_fib = fib_lookup.get(symbol, {})
                fib_key = f"fib_near_{gate_direction}"
                near = sym_fib.get(fib_key, pd.Series(dtype=bool)).get(ts, False)
                if not near:
                    continue

            score = 2  # Gate-TFs immer 2/2 wenn wir hier ankommen
            candidates.append((score, symbol, s))

        if not candidates:
            continue

        # Staeркstes Setup nehmen (hoechster Score, dann erstes gefundenes)
        candidates.sort(key=lambda x: -x[0])

        for _, symbol, s in candidates:
            if sum(1 for p in positions.values() if p) >= max_open:
                break

            entry = float(s["entry"])

            if fixed_margin_usd > 0:
                # Fixer Einsatz mit Hebel: Notional = margin × leverage
                notional = fixed_margin_usd * leverage
                qty = notional / entry
                # SL + TP symmetrisch: +/- fixed_margin_usd Gewinn/Verlust
                dist = fixed_margin_usd / qty  # Preisabstand fuer +/- $margin
                if s["direction"] == "long":
                    sl = entry - dist
                    tp1 = entry + dist   # 1:1, kein Teilausstieg
                    tp2 = tp1            # tp2 = tp1 (wird nicht separat benutzt)
                else:
                    sl = entry + dist
                    tp1 = entry - dist
                    tp2 = tp1
            else:
                sl = float(s["sl"])
                tp1 = float(s["tp1"])
                tp2 = _compute_tp2(entry, sl, s["direction"])
                qty = size_qty(state["balance"], entry, sl, pt_cfg)

            if qty <= 0:
                continue

            if max_book_x > 0:
                if _book_notional(positions) + qty * entry > state["balance"] * max_book_x:
                    continue

            positions[symbol] = MTFPosition.open(
                entry=entry, sl=sl, tp1=tp1, tp2=tp2, qty=qty,
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
    n = stats["total_trades"]
    wr = stats["win_rate_pct"]
    pnl = stats["net_pnl"]
    fees = stats["total_fees"]
    dd = stats["max_drawdown_pct"]
    exp = stats["expectancy"]
    pf = stats["profit_factor"]
    final = start_balance + pnl
    ret = (final / start_balance - 1) * 100

    print("\n" + "=" * 60)
    print("  MTF-BACKTEST-ERGEBNIS (1H+30m Gate, 15m+1m Kaskade, 50/50 Exit)")
    print("=" * 60)
    print(f"  Trades:        {n}  ({stats['wins']} Gewinner / {stats['losses']} Verlierer)")
    print(f"  Trefferquote:  {wr:.1f} %  (Break-even bei 2:1 eff. RR: ~29%)")
    print(f"  Netto-PnL:     {pnl:+.2f} USDT")
    print(f"  Gebuehren:     {fees:.2f} USDT")
    print(f"  Profit-Factor: {pf:.2f}" if pf else "  Profit-Factor: n/a")
    print(f"  Expectancy:    {exp:+.2f} USDT / Trade")
    print(f"  Max-Drawdown:  {dd:.1f} %")
    print(f"  Endkapital:    {final:.2f} USDT  ({ret:+.1f} %)")
    print("\n  PRO COIN:")
    for sym, s in stats.get("by_symbol", {}).items():
        print(f"  {sym:12s}: {s['total_trades']:4d} | WR {s['win_rate_pct']:.0f}% | {s['net_pnl']:+.2f} USDT")
    print("\n  LONG / SHORT:")
    for d, s in stats.get("by_direction", {}).items():
        print(f"  {d.upper():5s}: {s['total_trades']:4d} | WR {s['win_rate_pct']:.0f}% | {s['net_pnl']:+.2f} USDT")
    print("=" * 60)


def main() -> None:
    parser = argparse.ArgumentParser(description="MTF Divergenz-Backtest")
    parser.add_argument("--start", default="2026-01-01")
    parser.add_argument("--end", default=None)
    parser.add_argument("--balance", type=float, default=1000.0)
    parser.add_argument("--risk-pct", type=float, default=None,
                        help="Risiko pro Trade in %% der Balance (ueberschreibt config)")
    parser.add_argument("--margin-usd", type=float, default=0.0,
                        help="Fixer Einsatz pro Trade in USD (z.B. 10)")
    parser.add_argument("--leverage", type=float, default=1.0,
                        help="Hebel auf margin-usd (z.B. 30 -> 10*30=300 USD Notional)")
    parser.add_argument("--coins", nargs="+", default=None)
    parser.add_argument("--no-save", action="store_true")
    parser.add_argument("--fibonacci", action="store_true",
                        help="Fibonacci-Filter: Entry nur an 61.8%%/78.6%%-Levels des 1H-Swings")
    args = parser.parse_args()

    with open(PROJECT_ROOT / "config" / "config.yaml", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    if args.risk_pct is not None:
        config.setdefault("paper_trading", {})["risk_pct"] = args.risk_pct

    start_dt = pd.Timestamp(args.start, tz="utc")
    end_raw = args.end or pd.Timestamp.now(tz="utc").strftime("%Y-%m-%d")
    end_dt = pd.Timestamp(end_raw, tz="utc")
    symbols = args.coins or get_symbols(config)
    days = (end_dt - start_dt).days

    risk_pct_eff = config.get("paper_trading", {}).get("risk_pct", 1.0)
    risk_usd = args.balance * risk_pct_eff / 100

    print("=" * 60)
    fib_label = " + Fibonacci" if args.fibonacci else ""
    print(f"  MTF Divergenz-Backtest (1H+30m Gate, 15m+1m Kaskade{fib_label})")
    print("=" * 60)
    print(f"  Zeitraum:     {start_dt.date()} -> {end_dt.date()}  ({days} Tage)")
    print(f"  Coins:        {', '.join(symbols)}")
    print(f"  Startkapital: {args.balance:.0f} USDT")
    if args.margin_usd > 0:
        notional = args.margin_usd * args.leverage
        print(f"  Modus:        Fixer Einsatz {args.margin_usd:.0f} USD x {args.leverage:.0f}x = {notional:.0f} USD Notional (1:1 RR)")
    else:
        print(f"  Risiko/Trade: {risk_pct_eff:.1f}%  =  {risk_usd:.0f} USD SL-Betrag")
    print("=" * 60)

    symbol_data = {}
    htf_states = {}

    for symbol in symbols:
        print(f"\n  [{symbol}] Lade Daten...", flush=True)
        try:
            # TF-Daten: Gate (1H, 30m) + Entry-Kaskade (15m, 1m)
            # 15m und 1m unterliegen demselben ~70-Tage-Limit wie 5m auf BingX.
            htf = {}
            for tf in (_HTF_INTERVALS + _ENTRY_FILTER_TFS):
                show_progress = tf in ("1m",)
                df_htf = _load_and_prepare(symbol, tf, start_dt, end_dt, config,
                                           verbose=show_progress)
                if df_htf.empty and tf in ("15m", "1m"):
                    tf_start = end_dt - pd.Timedelta(days=70)
                    print(f"  [{symbol}] {tf}: Fallback auf {tf_start.date()} (BingX ~70-Tage-Limit)", flush=True)
                    df_htf = _load_and_prepare(symbol, tf, tf_start, end_dt, config,
                                               verbose=show_progress)
                if df_htf.empty:
                    print(f"  [{symbol}] ! {tf}: keine Daten")
                else:
                    print(f"  [{symbol}] {tf}: {len(df_htf)} Kerzen", flush=True)
                htf[f"df_{tf}"] = df_htf
                htf[f"ims_{tf}"] = _INTERVAL_MS.get(tf, 0)

            # Divergenz-Zustand fuer alle TFs vorberechnen
            for tf in (_HTF_INTERVALS + _ENTRY_FILTER_TFS):
                df_htf = htf[f"df_{tf}"]
                if not df_htf.empty:
                    print(f"  [{symbol}] {tf}: Divergenz-Zustand berechnen...", flush=True)
                    htf[f"state_{tf}"] = build_divergence_state(df_htf, config)
                else:
                    htf[f"state_{tf}"] = None

            htf_states[symbol] = htf

            # 5m Entry-TF
            # BingX speichert 5m-Daten nur ~70 Tage — automatisch Fallback.
            # HTF-Daten (4H/1H/30m) laufen laenger und werden fuer die
            # Divergenz-Vorheizung weiterhin ab start_dt geladen.
            print(f"  [{symbol}] 5m: Lade + Setups...", flush=True)
            df_5m, setups = _load_entry_tf(symbol, start_dt, end_dt, config)
            if df_5m.empty:
                entry_start = end_dt - pd.Timedelta(days=70)
                print(f"  [{symbol}] 5m: Fallback auf {entry_start.date()} (BingX ~70-Tage-Limit)", flush=True)
                df_5m, setups = _load_entry_tf(symbol, entry_start, end_dt, config)
            if df_5m.empty:
                print(f"  [{symbol}] ! 5m: keine Daten -- uebersprungen")
                continue
            n_valid = (setups["setup_valid"] == True).sum() if not setups.empty else 0
            print(f"  [{symbol}] 5m: {len(df_5m)} Kerzen, {n_valid} guelt. Setups", flush=True)
            symbol_data[symbol] = (df_5m, setups)

        except Exception as exc:
            import traceback
            print(f"  [{symbol}] ! Fehler: {exc}")
            traceback.print_exc()

    if not symbol_data:
        print("\n  Keine Daten. Abbruch.")
        return

    print(f"\n  Starte MTF-Simulation ({len(symbol_data)} Coins)...", flush=True)
    trades = simulate_mtf(
        symbol_data, htf_states, config,
        start_balance=args.balance,
        fixed_margin_usd=args.margin_usd,
        leverage=args.leverage,
        use_fibonacci=args.fibonacci,
    )
    print(f"  Fertig: {len(trades)} Trade-Ereignisse (inkl. Teilausstiege)", flush=True)

    if not args.no_save and trades:
        out = PROJECT_ROOT / "data" / "backtest_mtf_trades.csv"
        with out.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=_FIELDNAMES)
            writer.writeheader()
            writer.writerows(trades)
        print(f"\n  Gespeichert: {out}")

    stats = compute_stats(trades, start_balance=args.balance)
    _print_stats(stats, args.balance)


if __name__ == "__main__":
    main()
