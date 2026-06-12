# Strategie-Spezifikation: 5m Anchor-Trigger Scalp

> **Status:** Implementierungsreif (Startwerte gesetzt, im Paper Trading zu kalibrieren)
> **Zweck:** Übersetzt die Jason-Casper-Strategie in exakte, prüfbare Regeln.
> Dies ist der Bauplan für `signal_parser.py` und `strategy_engine.py`.

**Markt:** BTC/USDT Perpetual Futures (BingX)
**Timeframe:** 5 Minuten
**Setup-Typ:** Trend-Following Scalp, rein indikatorbasiert

---

## Legende

- ✅ **BELEGT** — durch Originalquelle gedeckt
- 🟢 **GESETZT** — Startwert von uns festgelegt, im Paper Trading zu kalibrieren
- Jeder gesetzte Wert hat einen `KALIBRIEREN`-Vermerk, wo Anpassung wahrscheinlich ist.

---

## 1. Dot-Signal (WaveTrend) — ✅ VERIFIZIERT gegen MCB-Chart

Nachbildung über **VuManChu Cipher B WaveTrend** (MCB-intern bestätigt durch
Live-Chart-Abgleich am 2026-06-12, siehe Hinweis unten).

```
n1 = 9             # Channel Length
n2 = 12            # Average Length
ap  = (high + low + close) / 3
esa = EMA(ap, n1)
d   = EMA(abs(ap - esa), n1)
ci  = (ap - esa) / (0.015 * d)
tci = EMA(ci, n2)
wt1 = tci
wt2 = SMA(wt1, 3)
```

- Grüner Dot (bullish) = wt1 kreuzt wt2 **von unten**
- Roter Dot (bearish)  = wt1 kreuzt wt2 **von oben**
- Schwellen: OB +60/+53 · OS -60/-53 (im MCB-Chart als Plots bestätigt)

> ✅ **WaveTrend-Abgleich erledigt (2026-06-12):** Live-Vergleich gegen
> Market Cipher B auf BINANCE:BTCUSDT 5m via TradingView-MCP. Ergebnis:
> Mit 9/12/3 weichen wt1/wt2 nur 1–4 % ab (erklärbar durch Bewegung der
> Live-Kerze zwischen den Messungen). Die ursprüngliche Annahme
> LazyBear 10/21/4 wich ~35 % ab und wurde verworfen. MCB-Plot-Zuordnung:
> „Lt Blue Wave" = wt1, „Blue Wave" = wt2.

---

## 2. Anker → Trigger Logik

- **Anker:** erste Welle, die über +60 (Long: unter -60) schließt
- **Trigger:** folgende Welle mit Dot, deren wt1-Extrem höher (Long) / tiefer (Short) liegt als der Anker

| Punkt | Status | Wert |
|-------|--------|------|
| Invalidierung des Setups | ✅ BELEGT | Null-Linien-Kreuzung der Momentumwelle beendet das Setup (kein fixes Kerzenfenster) |
| Wellen-Vergleich gemessen an | 🟢 GESETZT | wt1-Extremwert je Welle (Min bei Long / Max bei Short) |
| Mindest-Extrem der Triggerwelle | 🟢 GESETZT | `trigger_min_abs_wt1 = 50` — Triggerwellen mit \|wt1\| < 50 werden verworfen |
| Aufwärmphase-Schutz | 🟢 GESETZT | `warmup_candles = 100` — Setups in den ersten 100 Kerzen nach Datenbeginn sind ungültig (`warmup_artefact: true`) |

> `KALIBRIEREN`: 100 Kerzen ist Startannahme. Grund: EMAs (n2=12) sind am
> Datenanfang nicht eingeschwungen → verzerrte wt1-Werte (Beispiel
> anchor_wt1 −333). Solche Setups bekommen kein Trade-Level. Bei Live-Betrieb
> mit ausreichend History selten relevant, beim Backtest-Fensteranfang aber wichtig.

> `KALIBRIEREN`: Mindest-Extrem 50 ist eine Startannahme (nicht aus den Quellen).
> Grund: Visuelle Prüfung am 12.06. zeigte "späte" Trigger fast an der Null-Linie
> (wt1 29,3 / 14,1), die kaum noch Momentum hatten. 50 filtert diese heraus,
> lässt reguläre Trigger (|wt1| 56–74) bestehen. Im Paper Trading prüfen.

---

## 3. Entry — 🟢 GESETZT: Kerzenschluss

Quellen widersprechen sich (Kerzenschluss vs. Limit in den Wick).
**Festlegung für Bot-Start:** Entry beim **Schluss** der Trigger-Kerze nach
Dot-Bestätigung. Limit-in-Wick erst nach erfolgreichem Paper Trading erwägen.

---

## 4. MFI-Filter — ✅ BELEGT

> Quelle (5m Anchor-Trigger): „MFI-Wert ist höher als beim Anker-Punkt."

**Regel:** `MFI(Trigger-Punkt) > MFI(Anker-Punkt)` → Filter erfüllt.
Bei aktiver Divergenz entfällt der Filter.

> ℹ️ **Hinweis (Chart-Abgleich 2026-06-12):** Der „Mny Flow" in Market
> Cipher B ist **nicht** der Standard-MFI(14) — eigene, um 0 skalierte
> Variante (Beispiel: MCB −25,5 vs. Standard-MFI ~44). Absolute Werte sind
> daher nicht chart-vergleichbar. Für die **relative** Anker/Trigger-Regel
> (höher/tiefer als am Ankerpunkt) ist der Standard-MFI(14) ausreichend.

🟢 **Optionaler Zusatz (im Material belegt):** MFI-Nulllinien-Kreuzung von unten
als noch strengeres Long-Signal — als spätere Verschärfung testbar.

---

## 5. Stop Loss

| Punkt | Status | Wert |
|-------|--------|------|
| SL-Puffer | ✅ BELEGT (Bereich) / 🟢 GESETZT (Wert) | **0,3 %** unter Swing Low / über Swing High (Quellbereich 0,15–0,5 %) |
| Definition „letztes lokales Tief/Hoch" | 🟢 GESETZT | **Pivot mit 5 Kerzen Lookback je Seite** (in Quellen undefiniert) |
| Mindest-SL-Abstand | 🟢 GESETZT | `sl_min_pct = 0,3 %` — engere Stops sind ungültig (`sl_zu_eng: true`) |

> `KALIBRIEREN`: Alle drei Werte sind Startannahmen. Pivot-Fenster (3/5/7 Kerzen)
> und Puffer im Paper Trading gegen reale Stop-Outs prüfen. Mindest-SL 0,3 %:
> zu enge Stops (Beispiel 08:55-Short 0,117 %) werden durch Gebühren/Slippage
> unrentabel und fallen raus.

---

## 6. Take Profit (Exit)

**Exit: volle Position bei TP1 (RR 2:1). Kein Teilausstieg, kein Rest-Exit.**
✅ Produktentscheidung 2026-06-12.

| Punkt | Status | Wert |
|-------|--------|------|
| RR Standard | ✅ BELEGT | 2 : 1 |
| Exit der vollen Position | ✅ Produktentscheidung | bei TP1 |

> ~~RR bei Divergenz 3:1~~ · ~~Teilausstieg 50 %~~ · ~~SL nach TP1 auf Breakeven~~
> · ~~Rest-Exit MFI-Curvature (2 Kerzen Gegenrichtung)~~
>
> **GESTRICHEN 2026-06-12 (Vereinfachung für Bot-Start):** Mehrstufiger
> Exit (Teilausstieg + Breakeven-Zug + MFI-Curvature-Rest-Exit + TP2)
> entfällt. Eine einzige, eindeutig prüfbare Exit-Regel ist für den ersten
> Bot-Lauf robuster und im Paper Trading sauberer auswertbar. Bei Bedarf
> später reaktivierbar (Parameter in config.yaml auskommentiert erhalten).

---

## 7. Divergenz-Erkennung — 🟢 GESETZT (implementiert 2026-06-12)

> Definition (belegt): Bullisch = Preis Lower Low, Momentum Higher Low.
> Bearisch = Preis Higher High, Momentum Lower High.

| Punkt | Status | Wert |
|-------|--------|------|
| Max. Kerzen zwischen den 2 Tiefs/Hochs | 🟢 GESETZT | `divergence_window = 20` (in Quellen als Lücke benannt) |
| Vergleichsbasis | 🟢 GESETZT | wt1-Extremwerte (`comparison_basis`) |
| Pivot-Bestätigung der Extrema | 🟢 GESETZT | `pivot_lookback = 3` je Seite |
| Auswirkung bei aktiver Divergenz | ✅ BELEGT | hebt den MFI-Filter auf (Abschnitt 4) — **einzige** Auswirkung (TP2 gestrichen, s. Abschnitt 6) |

**Implementierungs-Entscheidungen** (`divergence_detector.py`):
- **Extrema-Findung:** Pivot-Logik wie der SL-Pivot — Kerze p ist Pivot-Tief,
  wenn ihr Low das Minimum der `pivot_lookback` Kerzen links UND rechts ist
  (Pivot-Hoch gespiegelt). Lookback 3 statt 5, da im 20-Kerzen-Fenster sonst
  zu wenige Pivots bestätigt werden.
- **Vergleichspunkte:** die zwei JÜNGSTEN bestätigten Pivots im Fenster
  `[trigger_idx − 20, trigger_idx]`. Long → Pivot-Tiefs (bullisch),
  Short → Pivot-Hochs (bearisch).
- **Momentum-Wert:** wt1 AN der Pivot-Kerze; Preis-Vergleich über low/high.
- **Repaint-sicher:** nur Pivots mit `p + lookback ≤ trigger_idx` (rechte Seite
  geschlossen) — kein Blick in die Zukunft, zusätzlich nur geschlossene Kerzen
  (Abschnitt 9). Detector verifiziert: 40 Long-/28 Short-Treffer über 999 Kerzen.

> `KALIBRIEREN`: `divergence_window = 20` und `pivot_lookback = 3` sind
> Startwerte. Da eine aktive Divergenz den MFI-Filter aufhebt (mehr Setups
> werden gültig), haben beide großen Einfluss — sorgfältig im Paper Trading testen.

---

## 8. Risiko & Hebel — 🟢 GESETZT

| Punkt | Wert |
|-------|------|
| Risiko pro Trade | 1 % des Kapitals (Casper-Standard) |
| Hebel | 3x (konservativer Start) |
| Max. offene Positionen | 1 |
| Max. Tagesverlust | -3 % → Bot pausiert |

---

## 9. Repaint-Schutz: nur geschlossene Kerzen — 🔒 ARCHITEKTUR-REGEL

**Regel:** Alle Indikator-Berechnungen (WaveTrend, MFI), Dot-Kreuzungen und
die Setup-Erkennung arbeiten ausschließlich auf **geschlossenen** Kerzen.
Die jeweils laufende Kerze wird bereits beim Datenladen verworfen.

**Grund (Vorfall 12.06.2026):** Ein Long-Setup (Anker 06:35, Trigger 07:55 UTC)
wurde auf der noch laufenden Kerze erkannt und verschwand mit dem
Kerzenschluss wieder — der Dot existierte nur auf der unfertigen Kerze
("Repainting"). Ein Bot hätte einen Trade auf ein Signal eröffnet, das es
nach Kerzenschluss nie gab.

**Umsetzung:** `fetch_binance_klines()` verwirft standardmäßig jede Kerze,
deren offizielle Schlusszeit in der Zukunft liegt (`drop_unclosed=True`).
Signale entstehen dadurch frühestens mit dem Schluss der Signalkerze —
konsistent mit der Entry-Regel „Kerzenschluss" (Abschnitt 3).
`drop_unclosed=False` ist nur für Anzeige-/Debug-Zwecke erlaubt, niemals
für handelbare Signale.

---

## Status-Übersicht

✅ **Verifiziert gegen MCB-Chart (2026-06-12):** Dot-Signal (WaveTrend 9/12/3)

✅ **Durch Quellen belegt:** Setup-Invalidierung, MFI-Filter,
RR-Ziele, Teilausstieg-Bereich, Breakeven-Regel, Divergenz-Definition

🟢 **Von uns gesetzt (im Paper Trading zu kalibrieren):** Entry-Timing,
SL-Puffer + Pivot-Fenster, Mindest-SL (0,3 %), Risiko/Hebel,
Wellen-Vergleichsbasis, Trigger-Mindest-Extrem (|wt1| ≥ 50),
Aufwärmphase (100 Kerzen), Divergenz-Fenster (20) + Divergenz-Pivot (3)

🔒 **Architektur-Regel:** Repaint-Schutz — nur geschlossene Kerzen (Abschnitt 9)

**Keine offenen 🔴 Punkte mehr — implementierungsreif für Paper Trading.**

---

## Wichtigster Hinweis für die Implementierung

Drei Werte (Pivot-Fenster, Rest-Exit, Divergenz-Fenster) sind **nicht** aus
Jasons Material belegt, sondern unsere begründeten Startannahmen. Der Bot wird
mit diesen Werten funktionieren — aber ob er Jasons Trades *nachbildet*, zeigt
erst der Abgleich im Paper Trading. Das ist erwartetes Verhalten, kein Fehler.

→ Diese drei Werte als **Konfigurationsparameter** (config.yaml) bauen, nicht
hart in den Code schreiben. So lassen sie sich ohne Code-Änderung kalibrieren.
