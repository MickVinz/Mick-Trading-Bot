"""
Paper-Trading-Engine (Multi-Coin): ein Zyklus pro Aufruf.

Ablauf je Zyklus (run_cycle), gegen EINE geteilte Balance:
    0. Tages-Rollover (UTC-Mitternacht) — geteilt über alle Coins.
    1. Pro Coin: BingX-Kerzen → WaveTrend → MFI → detect_setups → trade_levels.
    2. PHASE 1 — EXITS zuerst (über alle Coins): offene Position gegen letzte
       geschlossene Kerze prüfen, realisierten PnL auf die geteilte Balance buchen.
       Long:  low  ≤ SL → Exit @SL  |  high ≥ TP1 → Exit @TP1
       Short: high ≥ SL → Exit @SL  |  low  ≤ TP1 → Exit @TP1
       Beides in einer Kerze → SL zuerst (konservativ).
    3. RISK-GATE (geteilt): realized_pnl_today ≤ -max_daily_loss_pct% → keine
       neuen Entries bis nächster UTC-Tag.
    4. PHASE 2 — ENTRIES danach (über alle flachen Coins, KEIN Parallel-Limit):
       Setup.time == letzte Kerze UND setup_valid UND gültige Levels.
       Sizing: risk_usd = balance × risk_pct/100 | qty = risk_usd / |entry-sl|,
       gegen die nach Phase 1 aktualisierte geteilte Balance.

Zwei Phasen (erst alle Exits, dann alle Entries), damit das Entry-Sizing
reihenfolge-unabhängig gegen dieselbe, korrekt aktualisierte Balance läuft.

Keine Orders, kein fetch_bingx_balance — ausschließlich lesende Endpunkte.
_per_symbol ist ein reiner Test-Hook (None im Live-Betrieb).
"""

from __future__ import annotations

from typing import Optional, Tuple

import pandas as pd

from src.config_utils import get_symbols, resolve_config
from src.exchange.bingx_client import fetch_bingx_klines
from src.indicators.mfi import calculate_mfi
from src.indicators.wavetrend import calculate_wavetrend, detect_dots
from src.paper.journal import Journal
from src.paper.position import Position
from src.strategy.setup_detector import detect_setups
from src.strategy.trade_levels import calculate_trade_levels


# ---------------------------------------------------------------------------
# Öffentliche Hilfsfunktionen (auch direkt in Tests verwendbar)
# ---------------------------------------------------------------------------

def check_exit(
    position: Position,
    candle: pd.Series,
) -> Tuple[Optional[str], Optional[float]]:
    """
    Prüft ob SL oder TP1 auf dieser Kerze getroffen wurde.

    Rückgabe: ('sl', price) | ('tp1', price) | (None, None).
    Wenn BEIDE getroffen sind, gewinnt SL (konservative Annahme: Stop
    zuerst ausgelöst, bevor der Preis TP erreichte).
    """
    if position.direction == "long":
        sl_hit = candle["low"] <= position.sl
        tp_hit = candle["high"] >= position.tp1
    else:  # short
        sl_hit = candle["high"] >= position.sl
        tp_hit = candle["low"] <= position.tp1

    if sl_hit:
        return "sl", position.sl
    if tp_hit:
        return "tp1", position.tp1
    return None, None


def exit_costs(
    position: Position,
    exit_price: float,
    exit_reason: str,
    pt_cfg: dict,
) -> float:
    """
    Round-Trip-Handelskosten in USDT für das Schließen einer Position.

    Modell (konservativ, BingX-realistisch):
      - Taker-Gebühr auf BEIDE Legs (Entry = Market bei Kerzenschluss,
        SL = Stop-Market). `taker_fee_pct` je Seite.
      - Slippage (`slippage_pct`) auf JEDEN Market-Fill:
          * Entry slippt immer (Market-Order).
          * SL slippt (Stop-Market).
          * TP1 slippt NICHT — Limit-Order füllt am Zielpreis.

    Alle Kosten werden auf das jeweilige Notional (qty × Preis) gerechnet und
    beim Close in einem Betrag verrechnet (Entry bleibt unangetastet).
    Ohne Gebühren-Config (Defaults 0) ist das Ergebnis 0.0 — fee-frei.
    """
    taker = float(pt_cfg.get("taker_fee_pct", 0.0)) / 100.0
    slip = float(pt_cfg.get("slippage_pct", 0.0)) / 100.0
    qty = position.qty

    entry_notional = qty * position.entry
    exit_notional = qty * exit_price

    entry_fee = entry_notional * taker
    exit_fee = exit_notional * taker
    entry_slip = entry_notional * slip                      # Market-Entry: immer
    exit_slip = exit_notional * slip if exit_reason == "sl" else 0.0  # TP=Limit: 0

    return round(entry_fee + exit_fee + entry_slip + exit_slip, 8)


def size_qty(balance: float, entry: float, sl: float, pt_cfg: dict) -> float:
    """
    Berechnet die Positionsgröße in Coin-Einheiten.

    risk_usd  = balance × risk_pct / 100
    qty       = risk_usd / |entry − sl|
    Cap       = qty × entry ≤ balance × leverage_cap
    """
    risk_pct = float(pt_cfg.get("risk_pct", 1.0))
    leverage_cap = float(pt_cfg.get("leverage_cap", 3.0))

    sl_dist = abs(entry - sl)
    if sl_dist <= 0:
        return 0.0

    risk_usd = balance * risk_pct / 100
    qty = risk_usd / sl_dist
    max_qty = (balance * leverage_cap) / entry
    return round(min(qty, max_qty), 8)


# ---------------------------------------------------------------------------
# Geteilte Konto-Logik
# ---------------------------------------------------------------------------

def _rollover_if_new_day(state: dict) -> None:
    """Setzt die geteilten Tageswerte zurück, wenn ein neuer UTC-Tag begann."""
    today_utc = pd.Timestamp.now(tz="utc").strftime("%Y-%m-%d")
    if state.get("day", "") != today_utc:
        state["day"] = today_utc
        state["day_start_balance"] = state["balance"]
        state["realized_pnl_today"] = 0.0


def _gate_open(state: dict, config: dict) -> bool:
    """True, wenn das geteilte Tagesverlust-Limit NEUE Entries noch zulässt."""
    pt_cfg = config.get("paper_trading", {})
    max_loss_pct = float(pt_cfg.get("max_daily_loss_pct", 10.0))
    day_start = state.get("day_start_balance", state["balance"])
    pnl_today = state.get("realized_pnl_today", 0.0)
    daily_loss_pct = (pnl_today / day_start * 100) if day_start > 0 else 0.0
    return daily_loss_pct > -max_loss_pct


def _book_notional(journal: Journal) -> float:
    """Summe des Notionals (qty × entry) ALLER offenen Positionen (geteiltes Buch)."""
    total = 0.0
    for pos in journal.state.get("positions", {}).values():
        if pos is not None:
            total += pos.qty * pos.entry
    return total


def _is_new_candle(journal: Journal, symbol: str, last_ts: pd.Timestamp) -> bool:
    """Dedup pro Symbol: True, wenn last_ts neuer ist als die zuletzt verarbeitete Kerze."""
    last_processed = journal.get_last_candle_time(symbol)
    if last_processed is None:
        return True
    lp = pd.Timestamp(last_processed)
    lt = last_ts
    if lp.tzinfo is None and lt.tzinfo is not None:
        lp = lp.tz_localize("utc")
    elif lp.tzinfo is not None and lt.tzinfo is None:
        lt = lt.tz_localize("utc")
    return not (lp >= lt)


# ---------------------------------------------------------------------------
# Per-Symbol-Entscheidungen (testbar ohne BingX-Verbindung)
# ---------------------------------------------------------------------------

def _exit_symbol(symbol: str, df: pd.DataFrame, journal: Journal, pt_cfg: dict) -> dict:
    """
    PHASE-1-Baustein: prüft die offene Position eines Symbols gegen die letzte
    Kerze. Bucht ggf. realisierten NETTO-PnL (nach Gebühren + Slippage) auf die
    geteilte Balance, schließt die Position und schreibt den Trade.
    KEIN Dedup (das macht der Aufrufer).
    """
    state = journal.state
    last_candle = df.iloc[-1]
    last_ts = last_candle["timestamp"]
    result = {
        "symbol": symbol,
        "exit_reason": None,
        "exit_price": None,
        "entry_taken": False,
        "setup_found": False,
    }

    position = journal.get_position(symbol)
    if position is None:
        return result

    reason, exit_price = check_exit(position, last_candle)
    if reason is None:
        return result

    gross_pnl = position.pnl(exit_price)
    fees = exit_costs(position, exit_price, reason, pt_cfg)
    pnl_usd = gross_pnl - fees                         # NETTO nach Kosten
    state["balance"] += pnl_usd
    state["realized_pnl_today"] = state.get("realized_pnl_today", 0.0) + pnl_usd
    journal.set_position(symbol, None)
    journal.record_trade(
        symbol=symbol, position=position, exit_time=last_ts,
        exit_price=exit_price, exit_reason=reason,
        pnl_usd=pnl_usd, fees_usd=fees,
        balance_after=state["balance"],
    )
    # Sofort speichern: CSV-Zeile und state.json bleiben synchron auch bei Absturz.
    # Ohne das: Neustart sieht Position noch offen → doppelter Trade-Eintrag.
    journal.save_state()
    result["exit_reason"] = reason
    result["exit_price"] = exit_price
    return result


def _enter_symbol(
    symbol: str,
    df: pd.DataFrame,
    setups: pd.DataFrame,
    journal: Journal,
    config: dict,
    gate_open: bool,
) -> dict:
    """
    PHASE-2-Baustein: eröffnet ggf. eine Position für ein flaches Symbol.
    Nur wenn gate_open (geteiltes Tageslimit), Symbol flach und gültiges Setup
    auf der letzten Kerze. Sizing gegen die aktuelle geteilte Balance.

    Buch-Notional-Cap: das Gesamt-Notional ALLER offenen Positionen darf
    balance × max_book_notional_x nicht überschreiten (begrenzt das aggregierte
    Korrelations-Risiko über die 6 Coins). max_book_notional_x = 0 → Cap aus.
    KEIN Parallel-Count-Limit, KEIN Dedup (das macht der Aufrufer).
    """
    state = journal.state
    pt_cfg = config.get("paper_trading", {})
    last_ts = df.iloc[-1]["timestamp"]
    result = {"entry_taken": False, "setup_found": False, "book_cap_hit": False}

    if not gate_open or journal.get_position(symbol) is not None or setups.empty:
        return result

    mask = (
        (setups["setup_valid"] == True)            # noqa: E712
        & (setups["time"] == last_ts)
        & (setups["tp1"].notna())
    )
    if "sl_zu_eng" in setups.columns:
        mask &= setups["sl_zu_eng"] == False        # noqa: E712
    if "warmup_artefact" in setups.columns:
        mask &= setups["warmup_artefact"] == False   # noqa: E712
    valid = setups[mask]
    if valid.empty:
        return result

    result["setup_found"] = True
    s = valid.iloc[-1]
    entry, sl, tp1 = float(s["entry"]), float(s["sl"]), float(s["tp1"])
    qty = size_qty(state["balance"], entry, sl, pt_cfg)
    if qty <= 0:
        return result

    # Buch-Notional-Cap: aggregiertes Exposure über alle offenen Positionen.
    max_book_x = float(pt_cfg.get("max_book_notional_x", 0.0))
    if max_book_x > 0:
        new_notional = qty * entry
        if _book_notional(journal) + new_notional > state["balance"] * max_book_x:
            result["book_cap_hit"] = True
            return result

    journal.set_position(symbol, Position(
        entry=entry, sl=sl, tp1=tp1, qty=qty,
        direction=s["direction"], entry_time=last_ts,
        divergence=bool(s.get("divergence_active", False)),
    ))
    result["entry_taken"] = True
    return result


def _decide_symbol(
    symbol: str,
    df: pd.DataFrame,
    setups: pd.DataFrame,
    journal: Journal,
    config: dict,
    gate_open: bool,
) -> dict:
    """
    Vollständige Einzel-Symbol-Entscheidung (Exit + Entry + Dedup) für direkte
    Tests / Einzelaufrufe. run_cycle verwendet stattdessen _exit_symbol /
    _enter_symbol getrennt, um die zwei Phasen über alle Coins zu ordnen.
    """
    last_ts = df.iloc[-1]["timestamp"]
    result = {
        "symbol": symbol,
        "exit_reason": None,
        "exit_price": None,
        "entry_taken": False,
        "setup_found": False,
    }
    if not _is_new_candle(journal, symbol, last_ts):
        return result

    result.update(_exit_symbol(symbol, df, journal, config.get("paper_trading", {})))
    result.update(_enter_symbol(symbol, df, setups, journal, config, gate_open))
    journal.set_last_candle_time(symbol, last_ts.isoformat())
    return result


# ---------------------------------------------------------------------------
# Vollständige Multi-Coin-Pipeline (Live-Betrieb)
# ---------------------------------------------------------------------------

def _load_symbol_data(symbol: str, config: dict):
    """Lädt BingX-Kerzen für EIN Symbol und berechnet alle Indikatoren + Setups."""
    sym_cfg = resolve_config(config, symbol)
    bingx_symbol = symbol.replace("/", "-")
    interval = sym_cfg["market"]["timeframe"]

    df = fetch_bingx_klines(bingx_symbol, interval, limit=500)
    wt_cfg = sym_cfg["wavetrend"]
    df = calculate_wavetrend(df, n1=wt_cfg["n1"], n2=wt_cfg["n2"],
                             wt2_sma_length=wt_cfg["wt2_sma_length"])
    df = detect_dots(df)
    df = calculate_mfi(df, period=sym_cfg["mfi"]["period"])
    setups = detect_setups(df, sym_cfg)
    setups = calculate_trade_levels(df, setups, sym_cfg)
    return df, setups


def run_cycle(journal: Journal, config: dict, _per_symbol=None) -> dict:
    """
    Ein vollständiger Multi-Coin-Zyklus gegen die geteilte Balance.

    Phase 0: Tages-Rollover (geteilt).
    Phase 1: alle EXITS buchen (Balance aktualisieren).
    Phase 2: geteiltes Tageslimit-Gate, dann ENTRIES je flachem Coin.

    Dedup pro Symbol einmal pro Zyklus (last_candle_time): nur Coins mit einer
    NEUEN geschlossenen Kerze werden verarbeitet.

    _per_symbol (Test-Hook): {symbol: (df, setups)} überspringt das Laden.
    Rückgabe: {symbol: result-dict}.
    """
    _rollover_if_new_day(journal.state)
    symbols = get_symbols(config)

    # Daten beschaffen (Test-Hook oder live laden; pro Coin fehlertolerant).
    data = {}
    if _per_symbol is not None:
        data = _per_symbol
    else:
        for symbol in symbols:
            try:
                data[symbol] = _load_symbol_data(symbol, config)
            except Exception as exc:
                print(f"  ⚠ {symbol}: Daten-Fehler uebersprungen: {exc}", flush=True)

    # Dedup: nur Coins mit neuer geschlossener Kerze diesen Zyklus verarbeiten.
    fresh = {}
    for symbol, (df, setups) in data.items():
        if df is None or df.empty:
            continue
        if _is_new_candle(journal, symbol, df.iloc[-1]["timestamp"]):
            fresh[symbol] = (df, setups)

    # PHASE 1 — EXITS zuerst (Balance wird aktualisiert).
    pt_cfg = config.get("paper_trading", {})
    results = {}
    for symbol, (df, _setups) in fresh.items():
        results[symbol] = _exit_symbol(symbol, df, journal, pt_cfg)

    # PHASE 2 — ENTRIES gegen aktualisierte Balance, geteiltes Gate.
    gate = _gate_open(journal.state, config)
    for symbol, (df, setups) in fresh.items():
        entry_res = _enter_symbol(symbol, df, setups, journal, config, gate)
        results[symbol]["entry_taken"] = entry_res["entry_taken"]
        results[symbol]["setup_found"] = entry_res["setup_found"]
        results[symbol]["book_cap_hit"] = entry_res.get("book_cap_hit", False)
        journal.set_last_candle_time(symbol, df.iloc[-1]["timestamp"].isoformat())

    journal.save_state()
    return results
