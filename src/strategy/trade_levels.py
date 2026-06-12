"""
Trade-Level-Berechnung nach strategy_spec_5m_anchor_trigger.md:
Abschnitt 3 (Entry), 5 (Stop Loss), 6 (Take Profit).

NUR Berechnung — kein Trading, keine Orders.

Regeln aus der Spec:
    Entry = Schlusskurs der Trigger-Kerze (Abschnitt 3).
    SL    = Pivot-Tief (Long) bzw. Pivot-Hoch (Short) mit konfigurierbarem
            Lookback je Seite, plus/minus SL-Puffer in Prozent (Abschnitt 5).
    TP1   = Entry + RR_standard * Risiko (Long; Short gespiegelt).

Exit-Regel (Produktentscheidung 2026-06-12): VOLLE Position bei TP1
schließen. Kein Teilausstieg, kein Breakeven-Zug, kein Rest-Exit, kein
TP2/Divergenz-Ziel. Bewusst auf eine einzige Exit-Regel reduziert.

REPAINT-SCHUTZ beim Pivot: Ein Pivot-Tief bei Kerze p ist erst bestätigt,
wenn auch die `lookback` Kerzen NACH p geschlossen sind. Es werden daher
nur Pivots verwendet, deren Bestätigung zum Trigger-Zeitpunkt bereits
abgeschlossen war (p + lookback <= Trigger-Index) — kein Blick in die
Zukunft, der Bot hätte dieselben Daten gehabt.
"""

from typing import Optional

import pandas as pd


def _last_confirmed_pivot(
    df: pd.DataFrame,
    trigger_idx: int,
    lookback: int,
    find_low: bool,
    max_search: int = 100,
) -> Optional[float]:
    """
    Sucht das jüngste BESTÄTIGTE Pivot-Tief (find_low=True) bzw. Pivot-Hoch
    (find_low=False) vor der Trigger-Kerze.

    Pivot-Definition (Spec Abschnitt 5): Kerze p ist Pivot-Tief, wenn ihr
    Low das Minimum der `lookback` Kerzen links UND rechts von p ist.
    Bestätigt ist das erst `lookback` Kerzen später — deshalb p <= trigger_idx - lookback.

    Rückgabe: Preis des Pivots oder None, wenn im Suchfenster keiner liegt.
    """
    col = "low" if find_low else "high"
    newest_p = trigger_idx - lookback          # jüngster bestätigbarer Kandidat
    oldest_p = max(lookback, trigger_idx - max_search)

    for p in range(newest_p, oldest_p - 1, -1):
        window = df[col].iloc[p - lookback: p + lookback + 1]
        value = df[col].iloc[p]
        if find_low and value <= window.min():
            return float(value)
        if not find_low and value >= window.max():
            return float(value)
    return None


def calculate_trade_levels(
    df: pd.DataFrame,
    setups: pd.DataFrame,
    config: dict,
) -> pd.DataFrame:
    """
    Berechnet Entry, SL, TP1 (und TP2-Platzhalter) für jedes erkannte Setup.

    Parameter
    ---------
    df     : Kerzen-DataFrame mit 'timestamp', 'open', 'high', 'low', 'close'
             (dieselbe Serie, auf der die Setups erkannt wurden).
    setups : Ergebnis von detect_setups() — braucht 'time' und 'direction'.
    config : config.yaml als dict. Genutzt:
             stop_loss.pivot_lookback, stop_loss.buffer_pct,
             take_profit.rr_standard.

    Rückgabe
    --------
    Kopie von `setups` mit neuen Spalten:
        entry          Schlusskurs der Trigger-Kerze
        sl             Stop Loss (Pivot ± Puffer)
        tp1            Take Profit bei RR_standard (volle Position schließt hier)
        rr_ratio       verwendetes Risk-Reward-Verhältnis für TP1
        sl_risiko_pct  Abstand Entry→SL in Prozent des Entry-Preises
        sl_quelle      'pivot' oder 'fallback_fenster' (Transparenz)
    """
    sl_cfg = config["stop_loss"]
    tp_cfg = config["take_profit"]

    lookback = int(sl_cfg["pivot_lookback"])
    buffer_pct = float(sl_cfg["buffer_pct"])
    sl_min_pct = float(sl_cfg.get("sl_min_pct", 0))
    rr_standard = float(tp_cfg["rr_standard"])

    # Schneller Lookup: Timestamp -> Position in der Kerzen-Serie
    idx_by_time = {ts: i for i, ts in enumerate(df["timestamp"])}

    rows = []
    for _, s in setups.iterrows():
        # Aufwärmphase-Artefakte (Flag aus detect_setups): kein Trade-Level.
        if s.get("warmup_artefact", False):
            rows.append({**s, "entry": None, "sl": None, "tp1": None,
                         "rr_ratio": None, "sl_risiko_pct": None,
                         "sl_zu_eng": False, "sl_quelle": "warmup_artefact"})
            continue

        trigger_idx = idx_by_time[s["time"]]
        is_long = s["direction"] == "long"

        # Entry: Schlusskurs der Trigger-Kerze (Spec Abschnitt 3)
        entry = float(df["close"].iloc[trigger_idx])

        # Stop Loss: bestätigtes Pivot ± Puffer (Spec Abschnitt 5)
        pivot = _last_confirmed_pivot(df, trigger_idx, lookback, find_low=is_long)
        sl_quelle = "pivot"
        if pivot is None:
            # Fallback: tiefstes Low / höchstes High der letzten
            # (2*lookback+1) Kerzen vor dem Trigger — dokumentiert,
            # damit solche Fälle im Paper Trading auffallen.
            window = df.iloc[max(0, trigger_idx - 2 * lookback): trigger_idx + 1]
            pivot = float(window["low"].min() if is_long else window["high"].max())
            sl_quelle = "fallback_fenster"

        if is_long:
            sl = pivot * (1 - buffer_pct / 100)
            risk = entry - sl
        else:
            sl = pivot * (1 + buffer_pct / 100)
            risk = sl - entry

        if risk <= 0:
            # Pivot liegt auf der falschen Seite des Entrys (sehr selten,
            # z. B. nach steilem Move) — Setup nicht handelbar bewerten.
            rows.append({**s, "entry": round(entry, 2), "sl": round(sl, 2),
                         "tp1": None, "rr_ratio": None,
                         "sl_risiko_pct": None, "sl_zu_eng": False,
                         "sl_quelle": "ungueltig"})
            continue

        sl_risiko_pct = round(risk / entry * 100, 3)

        # Mindest-SL-Abstand: zu enge Stops (< sl_min_pct) werden durch
        # Gebühren/Slippage unrentabel — Setup als ungültig markieren.
        if sl_risiko_pct < sl_min_pct:
            rows.append({**s, "entry": round(entry, 2), "sl": round(sl, 2),
                         "tp1": None, "rr_ratio": None,
                         "sl_risiko_pct": sl_risiko_pct, "sl_zu_eng": True,
                         "sl_quelle": sl_quelle})
            continue

        # Take Profit: einziges Ziel TP1 (RR 2:1), volle Position schließt hier
        direction_sign = 1 if is_long else -1
        tp1 = entry + direction_sign * rr_standard * risk

        rows.append({
            **s,
            "entry": round(entry, 2),
            "sl": round(sl, 2),
            "tp1": round(tp1, 2),
            "rr_ratio": rr_standard,
            "sl_risiko_pct": sl_risiko_pct,
            "sl_zu_eng": False,
            "sl_quelle": sl_quelle,
        })

    return pd.DataFrame(rows)
