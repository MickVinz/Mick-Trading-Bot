# Design-Spec: Multi-Coin Paper Trading (6 Coins)

> **Status:** Entwurf zum Review — NICHT implementiert.
> **Datum:** 2026-06-13
> **Zweck:** Den 5m-Anchor-Trigger-Bot von einem Symbol (BTC) auf sechs Crypto-Perpetuals
> erweitern, mit gemeinsamem Konto und Multi-Coin-Dashboard.
> **Vorgänger:** `Jason Casper Markdown/strategy_spec_5m_anchor_trigger.md` (Strategie bleibt unverändert)

---

## 1. Scope

**6 Coins, alle BingX-Perpetuals, alle 24/7:**

| Coin | BingX-Symbol |
|---|---|
| Bitcoin | BTC-USDT |
| Ethereum | ETH-USDT |
| Solana | SOL-USDT |
| Dogecoin | DOGE-USDT |
| BNB | BNB-USDT |
| Cardano | ADA-USDT |

**Bewusst NICHT in diesem Spec** (YAGNI):
- Gold / S&P 500 / Nasdaq / Dow Jones — bräuchten eine zweite Datenquelle (eigene Phase).
- Echte Orders — bleibt reines Paper Trading.

---

## 2. Erfasste Entscheidungen (vom User)

| Thema | Entscheidung |
|---|---|
| Kapital-Modell | **Ein gemeinsames Konto** (eine geteilte Balance über alle Coins) |
| Parallele Positionen | **Kein Limit** — alle 6 dürfen gleichzeitig offen sein |
| Risiko pro Trade | 1 % der **aktuellen geteilten Balance** (unverändert) |
| Max. Tagesverlust | **10 %** (war 3 %) — pausiert NUR neue Entries |
| Risk-Reward (TP) | **2 : 1** (unverändert, Casper-belegt) |
| BTC | **bleibt** drin → 6 Coins gesamt |

> ⚠️ **Hinweis Risiko-Spreizung:** Ohne Parallel-Limit + je 1 % Risiko können bei Pech
> theoretisch alle 6 gleichzeitig verlieren (−6 % an gleichzeitigem Risiko), bevor das
> 10 %-Tageslimit überhaupt greift (es stoppt nur NEUE Entries). Das ist mit den
> obigen Entscheidungen konsistent und bewusst so gewählt.

---

## 3. Architektur — Ein Prozess, geteilte Balance

Ein einziger Loop-Prozess (passt zum bestehenden Watchdog, der genau einen Prozess startet)
iteriert je Kerzenschluss über alle 6 Coins.

```
                       ┌─────────────────────────────┐
                       │  run_paper_loop.py (1 Prozess) │
                       │  alle 5m: für jeden Coin …     │
                       └───────────────┬─────────────┘
                                       │
        ┌──────────────────────────────┼──────────────────────────────┐
        │ PHASE 1 — EXITS (alle Coins zuerst)                          │
        │   pro offener Position: SL/TP gegen letzte Kerze prüfen,      │
        │   realisierten PnL auf GETEILTE Balance buchen                │
        ├──────────────────────────────────────────────────────────────┤
        │ PHASE 2 — ENTRIES (alle Coins danach)                        │
        │   Tageslimit-Gate (geteilt) prüfen → wenn ok:                 │
        │   pro flachem Coin mit gültigem Setup auf letzter Kerze:      │
        │   Sizing = 1 % der (nach Phase 1 aktualisierten) Balance      │
        └──────────────────────────────────────────────────────────────┘
```

**Warum Exits ZUERST über alle Coins, dann Entries:**
Die Balance ändert sich nur bei realisiertem PnL (Exit). Werden erst alle Exits gebucht,
sizen alle neuen Entries gegen dieselbe, korrekt aktualisierte Balance — unabhängig von
der Reihenfolge der Coins. Saubere, reihenfolge-unabhängige Logik.

**Strategie-Module bleiben unverändert:** `wavetrend.py`, `mfi.py`, `setup_detector.py`,
`trade_levels.py`, `divergence_detector.py` nehmen weiterhin nur einen OHLCV-DataFrame.
Kein Eingriff in die Mathematik.

---

## 4. Config-Änderungen (`config/config.yaml`)

### 4.1 `market` → Symbol-Liste + optionale Per-Coin-Overrides

```yaml
market:
  timeframe: "5m"
  symbols:                      # NEU: Liste statt einzelnem symbol
    - "BTC/USDT"
    - "ETH/USDT"
    - "SOL/USDT"
    - "DOGE/USDT"
    - "BNB/USDT"
    - "ADA/USDT"

# NEU: optionale Overrides pro Coin. Anfangs LEER — globale Defaults gelten.
# Beispiel (auskommentiert), damit Struktur dokumentiert ist:
symbol_overrides:
  # "SOL/USDT":
  #   stop_loss:
  #     buffer_pct: 0.5        # SOL volatiler → größerer Puffer
  #   anchor_trigger:
  #     trigger_min_abs_wt1: 55
```

> **Abwärtskompatibilität:** Wird `symbols` nicht gefunden, aber das alte `symbol`,
> liest der Loader es als Einzel-Liste `[symbol]`. So brechen Tests/Altskripte nicht.

### 4.2 Geänderte Risiko-Parameter

```yaml
take_profit:
  rr_standard: 2.0              # unverändert (2:1, Casper-belegt)

risk:
  risk_per_trade_pct: 1.0
  leverage: 3
  max_open_positions: 6        # GEÄNDERT: kein echtes Limit (= Anzahl Coins)
  max_daily_loss_pct: 10.0     # GEÄNDERT (war 3.0)

paper_trading:
  start_balance: 1000.0        # eine geteilte Balance über alle Coins
  risk_pct: 1.0
  leverage_cap: 3.0
  max_daily_loss_pct: 10.0     # GEÄNDERT (war 3.0)
```

> Per-Coin-Override-Auflösung: Funktion `resolve_config(config, symbol)` liefert eine
> tiefe Verschmelzung aus globalem Config + `symbol_overrides[symbol]`. Die Strategie-Module
> bekommen weiterhin ein flaches `config`-Dict — sie merken vom Multi-Coin nichts.

---

## 5. State-Struktur (`data/state.json`)

**Eine geteilte Balance, Positionen pro Coin.**

```json
{
  "balance": 1000.0,
  "day": "2026-06-13",
  "day_start_balance": 1000.0,
  "realized_pnl_today": 0.0,
  "positions": {
    "BTC/USDT": { "...Position.to_dict()..." },
    "ETH/USDT": null,
    "SOL/USDT": null,
    "DOGE/USDT": null,
    "BNB/USDT": null,
    "ADA/USDT": null
  },
  "last_candle_time": {
    "BTC/USDT": "2026-06-13T11:55:00+00:00",
    "ETH/USDT": "2026-06-13T11:55:00+00:00"
  }
}
```

**Änderungen ggü. heute:**
- `open_position` (einzeln) → `positions` (Dict pro Symbol, `null` = flach).
- `last_candle_time` (einzeln) → Dict pro Symbol (jeder Coin dedupliziert eigenständig).
- `balance`, `day`, `day_start_balance`, `realized_pnl_today` bleiben **geteilt** (Konto-Ebene).

**Migration des bestehenden BTC-State:**
Beim ersten Start mit neuem Schema: existiert ein altes `state.json` mit `open_position`,
wird es nach `positions["BTC/USDT"]` migriert, `balance`/Tageswerte übernommen. Verlustfrei.

---

## 6. Trades-Protokoll (`data/trades.csv`)

**Neue erste Spalte `symbol`.** Sonst unverändert.

```
symbol, entry_time, exit_time, direction, entry, sl, tp1, exit_price,
exit_reason, qty, risk_pct, rr, pnl_usd, pnl_pct, balance_after, divergence
```

**Migration der bestehenden `trades.csv`:** Falls Datei ohne `symbol`-Spalte existiert,
einmalig `symbol="BTC/USDT"` in alle Altzeilen einfügen (Skript, einmalig, mit Backup).

---

## 7. Loop-Reihenfolge (`run_paper_loop.py`)

Pseudocode des neuen `run_cycle`:

```
1. Warte bis nächster 5m-Kerzenschluss + 5s Puffer.
2. Tages-Rollover (UTC): wenn neuer Tag → day_start_balance = balance,
   realized_pnl_today = 0.   (einmal pro Zyklus, geteilt)

3. Lade + berechne pro Coin (6×): fetch_bingx_klines → wavetrend → dots → mfi
   → detect_setups → calculate_trade_levels.   (resolve_config(symbol) je Coin)

4. PHASE 1 — EXITS (über alle 6):
   für jeden Coin mit offener Position:
     check_exit gegen letzte Kerze → bei Treffer:
       PnL auf geteilte balance + realized_pnl_today buchen,
       positions[symbol] = null, trade in trades.csv (mit symbol).

5. Tageslimit-Gate (geteilt):
   daily_loss_pct = realized_pnl_today / day_start_balance * 100
   gate_offen = daily_loss_pct > -max_daily_loss_pct (−10 %)

6. PHASE 2 — ENTRIES (über alle 6, nur wenn gate_offen):
   für jeden FLACHEN Coin mit gültigem Setup auf der letzten Kerze:
     qty = size_qty(balance, entry, sl)  # 1 % der aktuellen geteilten Balance
     positions[symbol] = neue Position.
   (Kein Parallel-Limit: jeder flache Coin darf rein.)

7. state speichern, Zyklus-Zusammenfassung pro Coin loggen.
```

> **API-Last:** 6× Klines-GET je 5-Minuten-Zyklus = unkritisch (BingX-Public-Limit
> liegt weit darüber). Coins sequentiell laden; ein Fehler bei einem Coin überspringt
> nur diesen Coin im Zyklus (try/except pro Coin), die anderen laufen weiter.

---

## 8. Dashboard (`scripts/dashboard.html`)

**Von 1 Coin → 6-Coin-Übersicht.** Datenquelle bleibt `state.json` + `trades.csv`
(jetzt mit neuer Struktur) + `watchdog_status.json` (unverändert).

Layout-Vorschlag:
- **Kopf:** geteilte Balance, Tages-PnL (€ + %), Tageslimit-Status (offen/pausiert),
  Anzahl offener Positionen (z.B. „3 / 6 offen").
- **Coin-Tabelle / Karten (6 Zeilen):** Symbol · Status (flach / Long@x / Short@x) ·
  Entry · SL · TP1 · aktueller Floating-PnL · letzter abgeschlossener Trade.
- **Trade-Historie:** `trades.csv` mit neuer `symbol`-Spalte, filterbar pro Coin.

> Design-Sprache (Space Grotesk, Glow-Cards, M-Logo, Roboter, deutsche Zeit, 0.85-Zoom)
> aus dem bestehenden Dashboard übernehmen — kein Redesign, nur Erweiterung um die Coin-Liste.

---

## 9. Betroffene Dateien (Umsetzungs-Übersicht)

| Datei | Änderung |
|---|---|
| `config/config.yaml` | `symbols`-Liste, `symbol_overrides`, Tageslimit 10 %, max_open 6 (RR bleibt 2:1) |
| `src/paper/journal.py` | `positions`-Dict, `last_candle_time`-Dict, `symbol` in trades.csv, Migration |
| `src/paper/paper_engine.py` | `run_cycle` (Phase-1-Exits / Phase-2-Entries über alle Coins), `resolve_config` |
| `scripts/run_paper_loop.py` | Loop ruft `run_cycle` (alle Coins) statt `run_once` (ein Coin) |
| `scripts/dashboard.html` | 6-Coin-Übersicht, geteilte Balance, Positionszähler |
| `scripts/migrate_state_v2.py` | **NEU**, einmalig: alten BTC-State + trades.csv migrieren (mit Backup) |
| `config/config.yaml`-Loader (überall, wo `config["market"]["symbol"]`) | auf `symbols` umstellen |

**Tests:** `test_paper_engine.py` erweitern (Multi-Coin-Zyklus, Exit-vor-Entry-Reihenfolge,
geteiltes Tageslimit, Migration). Bestehende Single-Coin-Tests via Abwärtskompatibilität grün halten.

---

## 10. Offene Kalibrierungspunkte (Paper Trading)

- Per-Coin-`buffer_pct` / `trigger_min_abs_wt1`: SOL/DOGE volatiler — Overrides ggf. nötig
  (Struktur ist da, Werte erst nach Beobachtung setzen).
- `sl_min_pct` (0,3 %) pro Coin: bei DOGE evtl. zu eng oder zu weit — beobachten.

---

## 11. Risiken & Sicherheit

- **Keine echten Orders** — ausschließlich lesende BingX-Endpunkte, wie bisher.
- **API-Keys** nur aus `.env` (gitignored) — keine Änderung am Key-Handling.
- **State-Migration** läuft mit Backup (`state.json.bak`, `trades.csv.bak`), verlustfrei umkehrbar.
- **Repaint-Schutz** (nur geschlossene Kerzen) gilt unverändert für jeden Coin.
