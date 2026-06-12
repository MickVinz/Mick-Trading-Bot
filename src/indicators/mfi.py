"""
Money Flow Index (MFI) — Standard-Definition, Periode 14 (anpassbar).

Der MFI ist ein volumengewichteter RSI:
    1. Typischer Preis  tp = (high + low + close) / 3
    2. Money Flow       mf = tp * volume
    3. Positiver Flow   = mf, wenn tp gestiegen ist, sonst 0
       Negativer Flow   = mf, wenn tp gefallen ist, sonst 0
    4. Money Ratio      = Summe(pos. Flow, n) / Summe(neg. Flow, n)
    5. MFI              = 100 - 100 / (1 + Money Ratio)

Wertebereich 0–100. Über 80 = überkauft, unter 20 = überverkauft
(klassische Lesart; die Strategie nutzt den MFI als Richtungsfilter:
MFI am Trigger-Punkt > MFI am Anker-Punkt).

WICHTIG für den Chart-Abgleich: Der "Money Flow" in Market Cipher B
ist NICHT der klassische MFI, sondern eine eigene (nicht offengelegte)
Variante. Die Spec legt bewusst den Standard-MFI(14) fest. Beim
visuellen Vergleich mit dem MarketCypher-Chart können die Kurven daher
ähnlich verlaufen, aber nicht identisch sein. Für exakten Abgleich in
TradingView den eingebauten Indikator "Money Flow Index" (Periode 14)
laden.
"""

import pandas as pd


def calculate_mfi(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """
    Berechnet den Money Flow Index.

    Parameter
    ---------
    df : DataFrame mit den Spalten 'high', 'low', 'close', 'volume'
         (chronologisch aufsteigend sortiert).
    period : Berechnungsperiode (Spec-Default: 14)

    Rückgabe
    --------
    Kopie des DataFrames mit neuer Spalte 'mfi'.
    """
    df = df.copy()

    # 1. Typischer Preis
    tp = (df["high"] + df["low"] + df["close"]) / 3

    # 2. Roher Money Flow
    raw_mf = tp * df["volume"]

    # 3. Flow nach Richtung der tp-Änderung aufteilen
    tp_change = tp.diff()
    positive_mf = raw_mf.where(tp_change > 0, 0.0)
    negative_mf = raw_mf.where(tp_change < 0, 0.0)
    # Bei unverändertem tp (Änderung == 0) zählt die Kerze in keine Richtung.

    # 4. Rollierende Summen über die Periode
    pos_sum = positive_mf.rolling(window=period).sum()
    neg_sum = negative_mf.rolling(window=period).sum()

    # 5. MFI; Division durch 0 abfangen (wenn es keine negativen Flows gab)
    money_ratio = pos_sum / neg_sum.replace(0, pd.NA)
    df["mfi"] = 100 - 100 / (1 + money_ratio)

    # Sonderfall: kein negativer Flow in der Periode -> maximal bullish = 100
    df.loc[(neg_sum == 0) & (pos_sum > 0), "mfi"] = 100.0

    df["mfi"] = df["mfi"].astype(float)

    return df
