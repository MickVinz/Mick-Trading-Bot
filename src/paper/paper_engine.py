"""
Paper-Trading-Engine: ein Pipeline-Durchlauf pro Aufruf.

Ablauf je Aufruf (run_once):
    1. BingX-Kerzen laden → WaveTrend → MFI → detect_setups → calculate_trade_levels
    2. EXIT ZUERST: offene Position gegen letzte geschlossene Kerze prüfen.
       Long:  low  ≤ SL → Exit @SL  |  high ≥ TP1 → Exit @TP1
       Short: high ≥ SL → Exit @SL  |  low  ≤ TP1 → Exit @TP1
       Beides in einer Kerze → SL zuerst (konservativ).
    3. ENTRY DANACH (nur wenn flach, max 1 Position):
       Setup.time == letzte Kerze UND setup_valid UND gültige Levels.
       Sizing: risk_usd = balance × risk_pct/100 | qty = risk_usd / |entry-sl|
       Notional-Cap: qty × entry ≤ balance × leverage_cap
    4. RISK-GATE: realized_pnl_today ≤ -max_daily_loss_pct% → kein Entry
       bis nächster UTC-Tag.

Keine Orders, kein fetch_bingx_balance — ausschließlich lesende Endpunkte.
_df / _setups sind reine Test-Hooks (beide None im Live-Betrieb).
"""

from __future__ import annotations

from typing import Optional, Tuple

import pandas as pd

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

    # SL hat Vorrang (wird zuerst geprüft)
    if sl_hit:
        return "sl", position.sl
    if tp_hit:
        return "tp1", position.tp1
    return None, None


def size_qty(balance: float, entry: float, sl: float, pt_cfg: dict) -> float:
    """
    Berechnet die Positionsgröße in BTC.

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
# Kernentscheidung (testbar ohne BingX-Verbindung)
# ---------------------------------------------------------------------------

def _make_decision(
    df: pd.DataFrame,
    setups: pd.DataFrame,
    journal: Journal,
    config: dict,
) -> dict:
    """
    Führt Exit-Prüfung und Entry-Entscheidung auf Basis bereits berechneter
    Daten durch. Mutiert journal.state und ruft journal.save_state() auf.

    df      : Kerzen-DataFrame mit allen Indikator-Spalten, chronologisch
              aufsteigend, nur geschlossene Kerzen.
    setups  : Ausgabe von calculate_trade_levels (kann leer sein).

    Rückgabe: dict mit exit_reason, exit_price, entry_taken, balance,
              setup_found.
    """
    state = journal.state
    last_candle = df.iloc[-1]
    last_ts = last_candle["timestamp"]
    today_utc = pd.Timestamp.now(tz="utc").strftime("%Y-%m-%d")

    result = {
        "exit_reason": None,
        "exit_price": None,
        "entry_taken": False,
        "setup_found": False,
        "balance": state["balance"],
    }

    # --- Tages-Rollover (UTC-Mitternacht) --------------------------------
    if state.get("day", "") != today_utc:
        state["day"] = today_utc
        state["day_start_balance"] = state["balance"]
        state["realized_pnl_today"] = 0.0

    # --- Dedup: Kerze schon verarbeitet? ----------------------------------
    last_processed = state.get("last_candle_time")
    if last_processed is not None:
        last_processed_ts = pd.Timestamp(last_processed)
        # Sicherstellen dass beide tz-aware oder beide tz-naive
        if last_processed_ts.tzinfo is None and last_ts.tzinfo is not None:
            last_processed_ts = last_processed_ts.tz_localize("utc")
        elif last_processed_ts.tzinfo is not None and last_ts.tzinfo is None:
            last_ts = last_ts.tz_localize("utc")
        if last_processed_ts >= last_ts:
            # Keine neue geschlossene Kerze seit letztem Durchlauf
            return result

    # --- 1. EXIT ZUERST ---------------------------------------------------
    position: Optional[Position] = state.get("open_position")
    if position is not None:
        reason, exit_price = check_exit(position, last_candle)
        if reason is not None:
            pnl_usd = position.pnl(exit_price)
            new_balance = state["balance"] + pnl_usd
            state["balance"] = new_balance
            state["realized_pnl_today"] = (
                state.get("realized_pnl_today", 0.0) + pnl_usd
            )
            state["open_position"] = None

            journal.record_trade(
                position=position,
                exit_time=last_ts,
                exit_price=exit_price,
                exit_reason=reason,
                balance_after=new_balance,
            )
            result["exit_reason"] = reason
            result["exit_price"] = exit_price
            result["balance"] = new_balance

    # --- 2. ENTRY (nur wenn flach) ----------------------------------------
    if state.get("open_position") is None and not setups.empty:
        pt_cfg = config.get("paper_trading", {})
        max_loss_pct = float(pt_cfg.get("max_daily_loss_pct", 3.0))

        day_start = state.get("day_start_balance", state["balance"])
        pnl_today = state.get("realized_pnl_today", 0.0)
        daily_loss_pct = (pnl_today / day_start * 100) if day_start > 0 else 0.0

        if daily_loss_pct <= -max_loss_pct:
            # Risk-Gate aktiv — kein neuer Entry bis nächster UTC-Tag
            pass
        else:
            # Gültige Setups auf der aktuellen (letzten geschlossenen) Kerze
            mask = (
                (setups["setup_valid"] == True)     # noqa: E712
                & (setups["time"] == last_ts)
                & (setups["tp1"].notna())
            )
            if "sl_zu_eng" in setups.columns:
                mask &= setups["sl_zu_eng"] == False   # noqa: E712
            if "warmup_artefact" in setups.columns:
                mask &= setups["warmup_artefact"] == False  # noqa: E712

            valid = setups[mask]

            if not valid.empty:
                result["setup_found"] = True
                s = valid.iloc[-1]  # bei mehreren: jüngstes nehmen

                entry = float(s["entry"])
                sl = float(s["sl"])
                tp1 = float(s["tp1"])

                qty = size_qty(state["balance"], entry, sl, pt_cfg)
                if qty > 0:
                    new_pos = Position(
                        entry=entry,
                        sl=sl,
                        tp1=tp1,
                        qty=qty,
                        direction=s["direction"],
                        entry_time=last_ts,
                        divergence=bool(s.get("divergence_active", False)),
                    )
                    state["open_position"] = new_pos
                    result["entry_taken"] = True

    state["last_candle_time"] = last_ts.isoformat()
    result["balance"] = state["balance"]
    journal.save_state()
    return result


# ---------------------------------------------------------------------------
# Vollständige Pipeline (Live-Betrieb)
# ---------------------------------------------------------------------------

def run_once(
    journal: Journal,
    config: dict,
    _df: Optional[pd.DataFrame] = None,
    _setups: Optional[pd.DataFrame] = None,
) -> dict:
    """
    Ein vollständiger Durchlauf der Paper-Trading-Pipeline.

    Im Normalfall (Live): lädt BingX-Kerzen, berechnet Indikatoren,
    erkennt Setups, berechnet Levels, ruft _make_decision auf.

    _df / _setups (Test-Hooks): wenn übergeben, überspringt der Aufruf
    den jeweiligen Schritt — nützlich für Tests ohne Netzwerkzugriff.
    Hinweis: Wenn _df übergeben wird, muss es bereits alle Indikator-
    Spalten (wt1, wt2, green_dot, red_dot, dot, mfi) enthalten, damit
    _setups korrekt berechnet werden kann, falls _setups=None.
    """
    market = config["market"]
    symbol = market["symbol"].replace("/", "-")   # "BTC/USDT" → "BTC-USDT"
    interval = market["timeframe"]

    # -- Daten & Indikatoren -----------------------------------------------
    if _df is not None:
        df = _df
    else:
        df = fetch_bingx_klines(symbol, interval, limit=500)

        wt_cfg = config["wavetrend"]
        df = calculate_wavetrend(
            df,
            n1=wt_cfg["n1"],
            n2=wt_cfg["n2"],
            wt2_sma_length=wt_cfg["wt2_sma_length"],
        )
        df = detect_dots(df)
        df = calculate_mfi(df, period=config["mfi"]["period"])

    # -- Setup-Erkennung & Trade-Levels ------------------------------------
    if _setups is not None:
        setups = _setups
    else:
        setups = detect_setups(df, config)
        setups = calculate_trade_levels(df, setups, config)

    return _make_decision(df, setups, journal, config)
