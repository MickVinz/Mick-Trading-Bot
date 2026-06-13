# Design-Spec (Phase 2, aufgeschoben): TradFi-Assets — Gold, S&P 500, Nasdaq, Dow Jones

> **Status:** Vorausschauende Planung — NICHT zur sofortigen Umsetzung.
> **Datum:** 2026-06-13
> **Zweck:** Die ursprünglich gewünschten Nicht-Crypto-Paare (Gold/XAU/USD, S&P 500,
> Nasdaq, Dow Jones) vollständig durchplanen, damit die Gesamt-Wunschliste abgedeckt ist.
> **Abhängigkeit:** Setzt die Multi-Coin-Basis voraus
> (`docs/superpowers/specs/2026-06-13-multi-coin-paper-trading-design.md`).
> **Warum aufgeschoben:** BingX-Crypto-API liefert diese Assets nicht — es braucht eine
> zweite Datenquelle + Handelszeit-Logik. Bewusst getrennt von der Crypto-Phase.

---

## 1. Scope dieser Phase

| Asset | Typ | BingX-Crypto-API? | Handelszeit |
|---|---|---|---|
| Gold (XAU/USD) | Rohstoff/FX | ❌ nein | So 23:00 – Fr 22:00 UTC, tägliche Pause |
| S&P 500 | Aktienindex | ❌ nein | US-Kernzeit + Futures-Sessions |
| Nasdaq 100 | Aktienindex | ❌ nein | US-Kernzeit + Futures-Sessions |
| Dow Jones | Aktienindex | ❌ nein | US-Kernzeit + Futures-Sessions |

(ETH ist bereits in der Crypto-Phase enthalten.)

---

## 2. Die zwei Kernprobleme (warum nicht „nur Symbol eintragen")

### 2.1 Datenquelle
Der bestehende `bingx_client.py` spricht `/openApi/swap/v2/quote/klines` — den
**Crypto-Perpetual**-Endpunkt. Gold/Indizes gibt es dort nicht. Es braucht einen
**zweiten Datenanbieter** hinter einer gemeinsamen Schnittstelle.

**Optionen (Entscheidung steht noch aus, im Brainstorming zu klären):**

| Option | Liefert 5m Gold/Indizes? | Aufwand | Haken |
|---|---|---|---|
| **Twelve Data** (API-Key, Gratis-Tier) | ja | mittel | Rate-Limit im Free-Tier (z. B. 8 req/min, 800/Tag) |
| **Alpha Vantage** (API-Key, Gratis-Tier) | ja (intraday begrenzt) | mittel | strenges Limit (25 req/Tag free) — für 5m-Loop knapp |
| **Polygon.io** | ja, sauber | mittel | Indizes/CFDs teils kostenpflichtig |
| **OANDA** (Practice-Account) | Gold + Indizes-CFDs, 5m | mittel-hoch | eigener Account + API-Token; CFD-Symbole ≠ echte Indizes |
| **TradingView-MCP** (vorhanden) | ja, jedes Symbol | gering im Code | braucht Desktop-App laufend, interaktiv → schlecht für 24/7-Autostart-Bot |

> **Architektur-Vorschlag:** `MarketDataSource`-Interface (gleiche DataFrame-Ausgabe wie
> `fetch_bingx_klines`: `timestamp, open, high, low, close, volume`, nur geschlossene
> Kerzen). Implementierungen: `BingxSource` (vorhanden, Crypto) + `TradFiSource` (neuer
> Anbieter). Pro Symbol in der Config hinterlegt, welche Quelle es bedient.

### 2.2 Handelszeiten (Crypto ist 24/7 — diese Assets NICHT)
Der Loop taktet aktuell stur an jeder 5m-UTC-Grenze. TradFi-Assets haben Pausen,
Wochenenden und Feiertage. Nötig:
- **Session-Kalender pro Asset** (offen/geschlossen je UTC-Zeit + Wochentag).
- Außerhalb der Session: **kein Datenabruf, kein Entry** für dieses Symbol (Crypto läuft
  parallel weiter).
- **Gap-Handling:** nach Wochenend-/Pausen-Lücken keine Fehl-Setups durch Kurssprünge
  (z. B. ersten N Kerzen nach Session-Start wie eine Warmup-Phase behandeln).

---

## 3. Vorgeschlagene Architektur-Erweiterungen (auf der Crypto-Basis)

| Baustein | Änderung ggü. Crypto-Phase |
|---|---|
| `MarketDataSource`-Interface | NEU — abstrahiert Datenherkunft; BingX + TradFi dahinter |
| `config.symbols` | je Symbol ein `source:` + optionaler `session:`-Kalender |
| `session_calendar.py` | NEU — `is_open(symbol, ts)`; Loop überspringt geschlossene Symbole |
| `run_cycle` | pro Symbol vor dem Laden `is_open` prüfen; geschlossene auslassen |
| Per-Asset-Kalibrierung | TradFi viel andere Volatilität/Tick-Size → eigene `symbol_overrides` |
| Dashboard | „geschlossen"-Status je Asset (Session zu) anzeigen |

> Die Strategie-Mathematik (WaveTrend/MFI/Anchor-Trigger) bleibt auch hier **unverändert** —
> sie ist asset-blind. Nur Daten-Herkunft + Handelszeit kommen dazu.

---

## 4. Offene Entscheidungen (vor Umsetzung im Brainstorming zu klären)

1. **Welcher Datenanbieter** (siehe Tabelle 2.1) — hängt an Budget, Rate-Limit, ob ein
   Account akzeptabel ist.
2. **Echte Indizes vs. CFDs/Futures-Proxies** — „S&P 500" als Index ist nicht direkt
   handelbar; später (Live) bräuchte es ES-Futures oder einen CFD. Fürs Paper Trading
   reicht eine Kursquelle, aber die Symbol-Definition muss eindeutig sein.
3. **Session-Kalender-Quelle** — fest kodiert (einfach) vs. vom Anbieter geliefert (genauer,
   inkl. Feiertage).
4. **Risiko-Einbindung** — laufen TradFi-Assets im selben gemeinsamen Konto wie die Coins
   oder in einem getrennten Topf? (Crypto-Phase: ein gemeinsames Konto.)

---

## 5. Empfohlene Reihenfolge

1. Crypto-Phase fertig bauen + im Paper Trading beobachten (Basis steht).
2. Datenanbieter wählen (eigenes kurzes Brainstorming zu den Optionen in 2.1).
3. `MarketDataSource`-Interface einziehen, `BingxSource` als erste Implementierung
   (reiner Refactor, kein Verhaltenswechsel — durch bestehende Tests abgesichert).
4. `TradFiSource` + `session_calendar.py` ergänzen.
5. Gold zuerst (eine Session-Logik, ein Asset) als Pilot, dann die 3 Indizes.

> **Kein Code in dieser Phase.** Dieses Dokument hält nur fest, dass die Nicht-Crypto-Assets
> bewusst geplant und auf später terminiert sind — damit die ursprüngliche Wunschliste
> (ETH + Gold + S&P 500 + Nasdaq + Dow Jones) vollständig adressiert ist.
