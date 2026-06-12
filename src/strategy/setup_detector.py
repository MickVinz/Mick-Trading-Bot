"""
Setup-Erkennung: Anker→Trigger-Logik nach strategy_spec_5m_anchor_trigger.md,
Abschnitt 2 (Anker→Trigger) und 4 (MFI-Filter).

NUR Erkennung — kein Entry, kein Exit, keine Orders.

Begriffe (so im Code umgesetzt):

    "Welle"        = ein Abwärts-Schwung von wt1 (Long-Seite) bzw.
                     Aufwärts-Schwung (Short-Seite). Eine Welle endet mit
                     einer Dot-Kreuzung (grün = wt1 dreht nach oben,
                     rot = wt1 dreht nach unten). Das wt1-Extrem der Welle
                     ist der tiefste (Long) bzw. höchste (Short) wt1-Wert
                     seit der letzten Dot-Kreuzung bzw. seit dem
                     Seitenwechsel.

    "Anker"        = erste Welle, deren wt1-Extrem unter -anchor_threshold
                     (Long) bzw. über +anchor_threshold (Short) liegt.
                     Taucht später eine NOCH tiefere/höhere Welle jenseits
                     der Schwelle auf, wird sie der neue Anker.

    "Trigger"      = eine auf den Anker folgende Welle MIT Dot, deren
                     wt1-Extrem höher (Long) bzw. tiefer (Short) liegt
                     als das Anker-Extrem (Spec: Vergleich gegen den Anker).

    "Invalidierung"= kreuzt wt1 die Null-Linie auf die andere Seite,
                     wird das Setup der aktuellen Seite verworfen.

Dokumentierte Interpretations-Entscheidung (Spec sagt nur "Anker-Punkt" /
"Trigger-Punkt"): Der MFI wird an der Kerze der jeweiligen DOT-KreUZUNG
gemessen — also dort, wo das Signal tatsächlich entsteht. Alternative
(Kerze des wt1-Extrems) läge meist nur 1–3 Kerzen daneben.

Divergenz ist der NÄCHSTE Schritt: Hier nur als Flag vorgesehen
(immer False). Sobald die Erkennung existiert, hebelt eine aktive
Divergenz den MFI-Filter aus (Spec Abschnitt 4).

REPAINT-SCHUTZ (Architektur-Regel, Spec Abschnitt 9): Dieses Modul
erwartet ausschließlich GESCHLOSSENE Kerzen. Die Datenbeschaffung
(fetch_binance_klines) verwirft die laufende Kerze standardmäßig —
niemals mit drop_unclosed=False erkannte Setups als handelbar werten.
"""

from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from src.strategy.divergence_detector import detect_divergence


@dataclass
class _SideState:
    """Interner Zustand der Erkennung für EINE Seite (long oder short)."""
    # Anker-Daten (None = noch kein Anker auf dieser Seite)
    anchor_extreme: Optional[float] = None   # wt1-Extremwert der Ankerwelle
    anchor_extreme_time: Optional[pd.Timestamp] = None
    anchor_dot_time: Optional[pd.Timestamp] = None
    anchor_mfi: Optional[float] = None       # MFI an der Anker-Dot-Kerze
    # Laufendes wt1-Extrem der aktuellen Welle
    wave_extreme: Optional[float] = None
    wave_extreme_time: Optional[pd.Timestamp] = None

    def reset(self) -> None:
        """Setup-Invalidierung: alles verwerfen (z. B. nach Null-Kreuzung)."""
        self.anchor_extreme = None
        self.anchor_extreme_time = None
        self.anchor_dot_time = None
        self.anchor_mfi = None
        self.wave_extreme = None
        self.wave_extreme_time = None

    def reset_wave(self) -> None:
        """Nur die laufende Welle zurücksetzen (nach jeder Dot-Kreuzung)."""
        self.wave_extreme = None
        self.wave_extreme_time = None


def detect_setups(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    """
    Erkennt alle vollständigen Anker→Trigger-Setups in einer Kerzen-Serie.

    Parameter
    ---------
    df : DataFrame mit den Spalten
         'timestamp', 'wt1', 'wt2', 'green_dot', 'red_dot', 'mfi'
         (chronologisch aufsteigend; siehe wavetrend.py / mfi.py).
    config : geladene config.yaml als dict. Genutzt werden:
         anchor_trigger.anchor_threshold       (z. B. 60)
         anchor_trigger.invalidate_on_zero_cross
         mfi.filter_enabled
         mfi.skip_filter_on_divergence

    Rückgabe
    --------
    DataFrame mit einer Zeile pro erkanntem Setup:
        time                Zeitpunkt der Trigger-Dot-Kerze (= Signalkerze)
        direction           'long' oder 'short'
        anchor_time         Zeitpunkt des wt1-Extrems der Ankerwelle
        anchor_wt1          wt1-Extremwert der Ankerwelle
        anchor_mfi          MFI an der Anker-Dot-Kerze
        trigger_wt1         wt1-Extremwert der Triggerwelle
        trigger_mfi         MFI an der Trigger-Dot-Kerze
        mfi_filter_passed   True/False (Long: trigger_mfi > anchor_mfi)
        divergence          Platzhalter, aktuell immer False (nächster Schritt)
        setup_valid         True, wenn MFI-Filter erfüllt ODER Divergenz aktiv
                            (oder Filter in der Config deaktiviert)
    """
    at_cfg = config["anchor_trigger"]
    mfi_cfg = config["mfi"]

    threshold = float(at_cfg["anchor_threshold"])
    trigger_min_abs_wt1 = float(at_cfg.get("trigger_min_abs_wt1", 0))
    warmup_candles = int(at_cfg.get("warmup_candles", 0))
    invalidate_on_zero_cross = bool(at_cfg.get("invalidate_on_zero_cross", True))
    filter_enabled = bool(mfi_cfg.get("filter_enabled", True))
    skip_filter_on_divergence = bool(mfi_cfg.get("skip_filter_on_divergence", True))

    long_state = _SideState()    # arbeitet auf der negativen wt1-Seite
    short_state = _SideState()   # arbeitet auf der positiven wt1-Seite

    setups: list[dict] = []
    prev_wt1: Optional[float] = None

    for row in df.itertuples(index=False):
        wt1 = row.wt1
        if pd.isna(wt1):
            continue  # Aufwärmphase der Indikatoren überspringen

        # ------------------------------------------------------------------
        # 1. Invalidierung: Null-Linien-Kreuzung beendet das Setup der Seite,
        #    die verlassen wurde (Spec Abschnitt 2, kein fixes Kerzenfenster).
        # ------------------------------------------------------------------
        if invalidate_on_zero_cross and prev_wt1 is not None:
            if prev_wt1 < 0 <= wt1:      # von negativ nach positiv gekreuzt
                long_state.reset()
            elif prev_wt1 > 0 >= wt1:    # von positiv nach negativ gekreuzt
                short_state.reset()
        prev_wt1 = wt1

        # ------------------------------------------------------------------
        # 2. Laufendes Wellen-Extrem fortschreiben
        #    (Long beobachtet wt1-Tiefs unter 0, Short wt1-Hochs über 0)
        # ------------------------------------------------------------------
        if wt1 < 0:
            if long_state.wave_extreme is None or wt1 < long_state.wave_extreme:
                long_state.wave_extreme = wt1
                long_state.wave_extreme_time = row.timestamp
        elif wt1 > 0:
            if short_state.wave_extreme is None or wt1 > short_state.wave_extreme:
                short_state.wave_extreme = wt1
                short_state.wave_extreme_time = row.timestamp

        # ------------------------------------------------------------------
        # 3. Dot-Kreuzung = Wellen-Ende -> Anker- oder Trigger-Prüfung
        # ------------------------------------------------------------------
        # Grüner Dot auf der negativen Seite -> Long-Logik
        if row.green_dot and wt1 < 0 and long_state.wave_extreme is not None:
            _process_wave_end(
                state=long_state, direction="long", row=row,
                threshold=-threshold,          # Anker: Extrem <= -60
                trigger_min_abs_wt1=trigger_min_abs_wt1,
                setups=setups,
                filter_enabled=filter_enabled,
                skip_filter_on_divergence=skip_filter_on_divergence,
            )

        # Roter Dot auf der positiven Seite -> Short-Logik
        if row.red_dot and wt1 > 0 and short_state.wave_extreme is not None:
            _process_wave_end(
                state=short_state, direction="short", row=row,
                threshold=threshold,           # Anker: Extrem >= +60
                trigger_min_abs_wt1=trigger_min_abs_wt1,
                setups=setups,
                filter_enabled=filter_enabled,
                skip_filter_on_divergence=skip_filter_on_divergence,
            )

    columns = ["time", "direction", "anchor_time", "anchor_wt1", "anchor_mfi",
               "trigger_wt1", "trigger_mfi", "mfi_filter_passed",
               "divergence_active", "divergence_type",
               "warmup_artefact", "setup_valid"]
    result = pd.DataFrame(setups, columns=columns)
    if result.empty:
        return result

    idx_by_time = {ts: i for i, ts in enumerate(df["timestamp"])}

    # ----------------------------------------------------------------------
    # Divergenz-Erkennung pro Setup (Spec Abschnitt 7). Eine aktive Divergenz
    # hebt den MFI-Filter auf (Spec Abschnitt 4) — das ist die einzige
    # verbleibende Auswirkung (TP2 wurde am 12.06. gestrichen).
    # setup_valid wird hier final neu berechnet:
    #   - Filter aus              -> gültig
    #   - Divergenz aktiv         -> gültig (MFI-Filter übersprungen)
    #   - sonst                   -> nur gültig, wenn MFI-Filter erfüllt
    # ----------------------------------------------------------------------
    div_active, div_type, valid = [], [], []
    for _, s in result.iterrows():
        d = detect_divergence(df, idx_by_time[s["time"]], s["direction"], config)
        div_active.append(d["divergence_active"])
        div_type.append(d["type"])

        if not filter_enabled:
            valid.append(True)
        elif d["divergence_active"] and skip_filter_on_divergence:
            valid.append(True)
        else:
            valid.append(bool(s["mfi_filter_passed"]))

    result["divergence_active"] = div_active
    result["divergence_type"] = div_type
    result["setup_valid"] = valid

    # ----------------------------------------------------------------------
    # Aufwärmphase-Schutz (zuletzt, überschreibt alles): Setups innerhalb der
    # ersten `warmup_candles` Kerzen nach Datenbeginn entstehen auf noch nicht
    # eingeschwungenen EMAs (verzerrte wt1-Werte, z.B. anchor_wt1 -333).
    # Als warmup_artefact markieren und setup_valid auf False zwingen.
    # ----------------------------------------------------------------------
    result["warmup_artefact"] = False
    if warmup_candles > 0 and len(df) > warmup_candles:
        warmup_cutoff = df["timestamp"].iloc[warmup_candles]
        is_artefact = result["time"] < warmup_cutoff
        result.loc[is_artefact, "warmup_artefact"] = True
        result.loc[is_artefact, "setup_valid"] = False

    return result


def _process_wave_end(
    state: _SideState,
    direction: str,
    row,
    threshold: float,
    trigger_min_abs_wt1: float,
    setups: list,
    filter_enabled: bool,
    skip_filter_on_divergence: bool,
) -> None:
    """
    Wird bei jeder Dot-Kreuzung aufgerufen (Wellen-Ende) und entscheidet:
    Ist die beendete Welle ein (neuer) Anker, ein Trigger oder keins von beiden?

    Long:  threshold = -60, "jenseits" heißt wave_extreme <= -60,
           "höher als Anker" heißt wave_extreme > anchor_extreme.
    Short: threshold = +60, gespiegelt.
    """
    extreme = state.wave_extreme
    extreme_time = state.wave_extreme_time
    is_long = direction == "long"

    # Liegt das Wellen-Extrem jenseits der Anker-Schwelle?
    beyond_threshold = extreme <= threshold if is_long else extreme >= threshold

    # Ist die Welle tiefer (Long) / höher (Short) als der aktuelle Anker?
    deeper_than_anchor = (
        state.anchor_extreme is not None
        and (extreme < state.anchor_extreme if is_long
             else extreme > state.anchor_extreme)
    )

    if beyond_threshold and (state.anchor_extreme is None or deeper_than_anchor):
        # Neuer Anker: erste Welle jenseits der Schwelle — oder eine noch
        # extremere Welle ersetzt den bisherigen Anker.
        state.anchor_extreme = extreme
        state.anchor_extreme_time = extreme_time
        state.anchor_dot_time = row.timestamp
        state.anchor_mfi = row.mfi   # MFI an der Dot-Kerze (siehe Docstring)

    elif state.anchor_extreme is not None and not deeper_than_anchor:
        # Trigger: Welle mit Dot, deren Extrem höher (Long) / tiefer (Short)
        # liegt als das Anker-Extrem (Spec: Vergleich gegen den Anker).

        # Mindest-Extrem-Filter (Spec Abschnitt 2, GESETZT/KALIBRIEREN):
        # "Späte" Trigger, deren Welle kaum noch Momentum hat (|wt1| zu nah
        # an der Null-Linie), werden verworfen.
        if abs(extreme) < trigger_min_abs_wt1:
            state.reset_wave()
            return
        if is_long:
            mfi_passed = row.mfi > state.anchor_mfi
        else:
            mfi_passed = row.mfi < state.anchor_mfi

        # Hinweis: divergence_active und das finale setup_valid werden im
        # Post-Processing von detect_setups gesetzt (dort steht der ganze
        # df für die Divergenz-Erkennung zur Verfügung). Hier nur Rohdaten +
        # MFI-Filter-Ergebnis ablegen.
        setups.append({
            "time": row.timestamp,
            "direction": direction,
            "anchor_time": state.anchor_extreme_time,
            "anchor_wt1": round(state.anchor_extreme, 2),
            "anchor_mfi": round(state.anchor_mfi, 2),
            "trigger_wt1": round(extreme, 2),
            "trigger_mfi": round(row.mfi, 2),
            "mfi_filter_passed": mfi_passed,
            "setup_valid": mfi_passed,   # vorläufig; Post-Processing überschreibt
        })

    # Nach jeder Dot-Kreuzung beginnt die nächste Welle "frisch"
    state.reset_wave()
