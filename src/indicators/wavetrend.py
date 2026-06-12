"""
WaveTrend Oscillator (VuManChu Cipher B) — Nachbildung des Market Cipher B
Dot-Signals.

Implementiert exakt nach strategy_spec_5m_anchor_trigger.md, Abschnitt 1.
Parameter 9/12/3 am 2026-06-12 per Live-Chart-Abgleich gegen Market Cipher B
verifiziert (Abweichung 1–4 %, Rest = Live-Kerzen-Bewegung):

    n1 = 9             # Channel Length
    n2 = 12            # Average Length
    ap  = (high + low + close) / 3
    esa = EMA(ap, n1)
    d   = EMA(abs(ap - esa), n1)
    ci  = (ap - esa) / (0.015 * d)
    tci = EMA(ci, n2)
    wt1 = tci
    wt2 = SMA(wt1, 3)

Dot-Logik:
    Grüner Dot (bullish) = wt1 kreuzt wt2 von unten
    Roter Dot  (bearish) = wt1 kreuzt wt2 von oben
"""

import pandas as pd


def calculate_wavetrend(
    df: pd.DataFrame,
    n1: int = 9,
    n2: int = 12,
    wt2_sma_length: int = 3,
) -> pd.DataFrame:
    """
    Berechnet wt1 und wt2 aus einem OHLC-DataFrame.

    Parameter
    ---------
    df : DataFrame mit den Spalten 'high', 'low', 'close'
         (eine Zeile pro Kerze, chronologisch aufsteigend sortiert).
    n1 : Channel Length (Spec-Default: 9)
    n2 : Average Length (Spec-Default: 12)
    wt2_sma_length : SMA-Länge für wt2 (Spec-Default: 3)

    Rückgabe
    --------
    Kopie des DataFrames mit zwei neuen Spalten: 'wt1' und 'wt2'.

    Hinweis: pandas ewm(span=n, adjust=False) entspricht exakt der
    Pine-Script-Funktion ta.ema(quelle, n) — gleiche rekursive Formel
    mit alpha = 2 / (n + 1).
    """
    df = df.copy()

    # ap = HLC3 (typischer Preis der Kerze)
    ap = (df["high"] + df["low"] + df["close"]) / 3

    # esa = EMA des typischen Preises
    esa = ap.ewm(span=n1, adjust=False).mean()

    # d = EMA der absoluten Abweichung von esa
    d = (ap - esa).abs().ewm(span=n1, adjust=False).mean()

    # ci = normalisierte Abweichung (0.015 ist die LazyBear-Konstante)
    ci = (ap - esa) / (0.015 * d)

    # tci = geglättetes ci -> das ist wt1
    df["wt1"] = ci.ewm(span=n2, adjust=False).mean()

    # wt2 = einfacher gleitender Durchschnitt von wt1
    df["wt2"] = df["wt1"].rolling(window=wt2_sma_length).mean()

    return df


def detect_dots(df: pd.DataFrame) -> pd.DataFrame:
    """
    Erkennt Dot-Kreuzungen zwischen wt1 und wt2.

    Erwartet einen DataFrame, der bereits 'wt1' und 'wt2' enthält
    (siehe calculate_wavetrend).

    Fügt drei Spalten hinzu:
        'green_dot' : True, wenn wt1 in dieser Kerze wt2 von unten kreuzt
        'red_dot'   : True, wenn wt1 in dieser Kerze wt2 von oben kreuzt
        'dot'       : 'grün' / 'rot' / '-' (lesbare Zusammenfassung)

    Kreuzung "von unten" heißt: in der Vorkerze war wt1 <= wt2,
    in der aktuellen Kerze ist wt1 > wt2. (Umgekehrt für "von oben".)
    """
    df = df.copy()

    prev_wt1 = df["wt1"].shift(1)
    prev_wt2 = df["wt2"].shift(1)

    df["green_dot"] = (prev_wt1 <= prev_wt2) & (df["wt1"] > df["wt2"])
    df["red_dot"] = (prev_wt1 >= prev_wt2) & (df["wt1"] < df["wt2"])

    df["dot"] = "-"
    df.loc[df["green_dot"], "dot"] = "grün"
    df.loc[df["red_dot"], "dot"] = "rot"

    return df
