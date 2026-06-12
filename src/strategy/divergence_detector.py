"""
Divergenz-Erkennung nach strategy_spec_5m_anchor_trigger.md, Abschnitt 7.

DEFINITION (belegt):
    Bullische Divergenz (für Long): Preis bildet ein Lower Low (LL),
        während wt1 ein Higher Low (HL) bildet.
    Bearische Divergenz (für Short): Preis bildet ein Higher High (HH),
        während wt1 ein Lower High (LH) bildet.

Eine Divergenz vergleicht IMMER zwei Punkte: ein älteres und ein jüngeres
lokales Extrem. Der Preis und das Momentum (wt1) laufen zwischen diesen
beiden Punkten auseinander.

WICHTIGSTE IMPLEMENTIERUNGS-ENTSCHEIDUNGEN (Spec ließ Details offen):

1. WIE wird ein "Tief"/"Hoch" gefunden?
   Über Pivot-Extrema: Kerze p ist ein Pivot-Tief, wenn ihr Low das Minimum
   der `pivot_lookback` Kerzen links UND rechts ist (Pivot-Hoch gespiegelt).
   Das ist dieselbe Logik wie der SL-Pivot in trade_levels.py, nur mit
   kleinerem Lookback (3 statt 5), weil im 20-Kerzen-Fenster sonst zu wenige
   Pivots bestätigt werden.

2. WELCHE zwei Punkte werden verglichen?
   Die beiden JÜNGSTEN bestätigten Pivots im Fenster
   [trigger_idx - divergence_window, trigger_idx]. Der jüngere Pivot liegt
   nahe am Trigger, der ältere ist der vorhergehende Pivot derselben Art.

3. REPAINT-SCHUTZ:
   Ein Pivot bei Kerze p ist erst bestätigt, wenn die `pivot_lookback`
   Kerzen NACH p geschlossen sind. Es werden nur Pivots mit
   p + pivot_lookback <= trigger_idx verwendet — kein Blick in die Zukunft.
   Zusätzlich liefert die Datenquelle ohnehin nur geschlossene Kerzen
   (Repaint-Schutz, Spec Abschnitt 9).

4. VERGLEICHSBASIS Momentum:
   wt1-Wert AN der Pivot-Kerze (config comparison_basis = "wt1_extreme").
   Preis-Vergleich über low/high der Pivot-Kerze.
"""

from typing import Optional

import pandas as pd


def _find_confirmed_pivots(
    df: pd.DataFrame,
    trigger_idx: int,
    window: int,
    lookback: int,
    find_low: bool,
) -> list:
    """
    Sammelt alle bestätigten Pivot-Extrema im Fenster vor dem Trigger.

    Rückgabe: Liste von dicts {idx, time, price, wt1}, von ALT nach JUNG
    sortiert (aufsteigender Index). find_low=True -> Pivot-Tiefs (low),
    find_low=False -> Pivot-Hochs (high).
    """
    col = "low" if find_low else "high"
    # Jüngster bestätigbarer Pivot: trigger_idx - lookback (rechte Seite zu).
    newest_p = trigger_idx - lookback
    # Ältester Pivot im Divergenz-Fenster; lookback links muss auch passen.
    oldest_p = max(lookback, trigger_idx - window)

    pivots = []
    for p in range(oldest_p, newest_p + 1):
        win = df[col].iloc[p - lookback: p + lookback + 1]
        value = df[col].iloc[p]
        is_pivot = (value <= win.min()) if find_low else (value >= win.max())
        if is_pivot:
            pivots.append({
                "idx": p,
                "time": df["timestamp"].iloc[p],
                "price": float(value),
                "wt1": float(df["wt1"].iloc[p]),
            })
    return pivots


def detect_divergence(
    df: pd.DataFrame,
    trigger_idx: int,
    direction: str,
    config: dict,
) -> dict:
    """
    Prüft, ob am Trigger-Punkt eine Divergenz aktiv ist.

    Parameter
    ---------
    df          : Kerzen-DataFrame mit 'timestamp','high','low','close','wt1'.
    trigger_idx : Position der Trigger-Kerze in df.
    direction   : 'long' (sucht bullische Div.) oder 'short' (bearische).
    config      : config.yaml als dict. Genutzt:
                  divergence.divergence_window, divergence.pivot_lookback.

    Rückgabe (dict)
    ---------------
    {
      "divergence_active": bool,
      "type": "bullish" | "bearish" | None,
      "point_old":  {time, price, wt1} | None,   # älteres Extrem
      "point_new":  {time, price, wt1} | None,   # jüngeres Extrem
    }
    """
    div_cfg = config["divergence"]
    window = int(div_cfg["divergence_window"])
    lookback = int(div_cfg["pivot_lookback"])

    is_long = direction == "long"
    # Long -> bullische Divergenz an Pivot-TIEFS; Short -> bearisch an Pivot-HOCHS.
    pivots = _find_confirmed_pivots(df, trigger_idx, window, lookback,
                                    find_low=is_long)

    inactive = {"divergence_active": False, "type": None,
                "point_old": None, "point_new": None}

    if len(pivots) < 2:
        return inactive  # zu wenige Extrema im Fenster -> keine Divergenz prüfbar

    # Die zwei jüngsten Pivots vergleichen (älter = vorletzter, neu = letzter)
    p_old, p_new = pivots[-2], pivots[-1]

    if is_long:
        # Bullisch: Preis Lower Low UND wt1 Higher Low
        price_ll = p_new["price"] < p_old["price"]
        wt1_hl = p_new["wt1"] > p_old["wt1"]
        active = price_ll and wt1_hl
        div_type = "bullish"
    else:
        # Bearisch: Preis Higher High UND wt1 Lower High
        price_hh = p_new["price"] > p_old["price"]
        wt1_lh = p_new["wt1"] < p_old["wt1"]
        active = price_hh and wt1_lh
        div_type = "bearish"

    if not active:
        return inactive

    return {
        "divergence_active": True,
        "type": div_type,
        "point_old": {"time": p_old["time"], "price": round(p_old["price"], 2),
                      "wt1": round(p_old["wt1"], 2)},
        "point_new": {"time": p_new["time"], "price": round(p_new["price"], 2),
                      "wt1": round(p_new["wt1"], 2)},
    }
