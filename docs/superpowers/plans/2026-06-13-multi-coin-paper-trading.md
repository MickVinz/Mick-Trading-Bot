# Multi-Coin Paper Trading Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Den 5m-Anchor-Trigger-Bot von einem Symbol (BTC) auf sechs Crypto-Perpetuals (BTC, ETH, SOL, DOGE, BNB, ADA) erweitern — gemeinsames Konto, kein Parallel-Limit, RR 2:1, Tageslimit 10 %, plus Multi-Coin-Dashboard.

**Architecture:** Ein einziger Loop-Prozess iteriert je 5m-Kerzenschluss über alle Coins. Phase 1: alle Exits buchen (geteilte Balance aktualisieren). Phase 2: Tageslimit-Gate prüfen, dann pro flachem Coin mit gültigem Setup einsteigen (Sizing gegen aktualisierte Balance). State: eine geteilte Balance + `positions`-Dict pro Coin. Strategie-Module bleiben unverändert.

**Tech Stack:** Python 3, pandas, PyYAML, requests. Tests: eigener Runner (kein pytest), direkt per `python scripts/test_*.py` ausführbar — Stil von `scripts/test_paper_engine.py` übernehmen.

**Spec:** `docs/superpowers/specs/2026-06-13-multi-coin-paper-trading-design.md`

---

## File Structure

| Datei | Verantwortung | Aktion |
|---|---|---|
| `src/config_utils.py` | Config laden, Symbol-Liste lesen, Per-Coin-Overrides auflösen | **NEU** |
| `config/config.yaml` | `symbols`-Liste, `symbol_overrides`, Tageslimit 10 %, max_open 6 | Modify |
| `src/paper/journal.py` | Multi-Coin-State (`positions`-Dict, `last_candle_time`-Dict), `symbol`-Spalte in trades.csv, Migration v1→v2 | Modify |
| `src/paper/paper_engine.py` | `run_cycle` (zwei Phasen über alle Coins), symbol-bewusste Exit/Entry-Helfer | Modify |
| `scripts/run_paper_loop.py` | Loop ruft `run_cycle` über alle Coins | Modify |
| `scripts/migrate_state_v2.py` | Einmalige Migration alter `state.json`/`trades.csv` (mit Backup) | **NEU** |
| `scripts/dashboard.html` | 6-Coin-Übersicht, geteilte Balance, Positionszähler | Modify |
| `scripts/test_config_utils.py` | Tests für config_utils | **NEU** |
| `scripts/test_paper_engine.py` | bestehende Tests auf Multi-Coin-Schema umstellen + neue Multi-Coin-Tests | Modify |

**Reihenfolge:** config_utils → journal → engine → loop → migration → dashboard. Jede Stufe ist für sich testbar.

---

## Task 1: config_utils — Symbol-Liste + Per-Coin-Override-Auflösung

**Files:**
- Create: `src/config_utils.py`
- Test: `scripts/test_config_utils.py`

- [ ] **Step 1: Write the failing tests**

```python
# scripts/test_config_utils.py
"""
Tests für src/config_utils.py — Symbol-Liste + Per-Coin-Override-Auflösung.
Direkt ausführbar: python scripts/test_config_utils.py
"""
import sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config_utils import get_symbols, resolve_config

_pass = 0
_fail = 0


def _assert(cond, label):
    global _pass, _fail
    if cond:
        _pass += 1
        print(f"  ✓  {label}")
    else:
        _fail += 1
        print(f"  ✗  FEHLER: {label}")


def test_get_symbols_list():
    cfg = {"market": {"symbols": ["BTC/USDT", "ETH/USDT"], "timeframe": "5m"}}
    _assert(get_symbols(cfg) == ["BTC/USDT", "ETH/USDT"],
            "1. get_symbols: liest symbols-Liste")


def test_get_symbols_legacy_single():
    cfg = {"market": {"symbol": "BTC/USDT", "timeframe": "5m"}}
    _assert(get_symbols(cfg) == ["BTC/USDT"],
            "2. get_symbols: altes single symbol → Einzel-Liste (Abwärtskompat)")


def test_resolve_config_no_override():
    cfg = {
        "market": {"symbols": ["BTC/USDT"], "timeframe": "5m"},
        "stop_loss": {"buffer_pct": 0.3},
    }
    resolved = resolve_config(cfg, "BTC/USDT")
    _assert(resolved["stop_loss"]["buffer_pct"] == 0.3,
            "3. resolve_config ohne Override: globaler Wert bleibt")


def test_resolve_config_with_override():
    cfg = {
        "market": {"symbols": ["BTC/USDT", "SOL/USDT"], "timeframe": "5m"},
        "stop_loss": {"buffer_pct": 0.3, "sl_min_pct": 0.3},
        "symbol_overrides": {
            "SOL/USDT": {"stop_loss": {"buffer_pct": 0.5}},
        },
    }
    resolved = resolve_config(cfg, "SOL/USDT")
    _assert(resolved["stop_loss"]["buffer_pct"] == 0.5,
            "4. resolve_config: Override überschreibt buffer_pct (0.5)")
    _assert(resolved["stop_loss"]["sl_min_pct"] == 0.3,
            "4. resolve_config: nicht-überschriebener Wert bleibt (sl_min_pct 0.3)")


def test_resolve_config_does_not_mutate_global():
    cfg = {
        "market": {"symbols": ["SOL/USDT"], "timeframe": "5m"},
        "stop_loss": {"buffer_pct": 0.3},
        "symbol_overrides": {"SOL/USDT": {"stop_loss": {"buffer_pct": 0.9}}},
    }
    resolve_config(cfg, "SOL/USDT")
    _assert(cfg["stop_loss"]["buffer_pct"] == 0.3,
            "5. resolve_config mutiert das globale Config NICHT")


def main():
    print("config_utils-Tests\n")
    test_get_symbols_list()
    test_get_symbols_legacy_single()
    test_resolve_config_no_override()
    test_resolve_config_with_override()
    test_resolve_config_does_not_mutate_global()
    print(f"\n{_pass + _fail} Tests: {_pass} bestanden, {_fail} fehlgeschlagen.")
    if _fail > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python scripts/test_config_utils.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.config_utils'`

- [ ] **Step 3: Write the implementation**

```python
# src/config_utils.py
"""
Hilfsfunktionen rund um die Multi-Coin-Konfiguration.

- get_symbols(config): liefert die Liste der zu handelnden Symbole.
  Liest market.symbols (neu) oder fällt auf market.symbol (alt) zurück.
- resolve_config(config, symbol): liefert eine Kopie des Config, in die ein
  evtl. vorhandener symbol_overrides[symbol]-Block tief eingemischt wurde.
  Die Strategie-Module bekommen dadurch weiterhin ein flaches Config-Dict.
"""
from __future__ import annotations

import copy
from typing import Any


def get_symbols(config: dict) -> list[str]:
    """
    Liste der Handels-Symbole. Bevorzugt market.symbols (Liste).
    Abwärtskompatibel: market.symbol (Einzelwert) → [symbol].
    """
    market = config.get("market", {})
    symbols = market.get("symbols")
    if symbols:
        return list(symbols)
    single = market.get("symbol")
    if single:
        return [single]
    raise ValueError("config.market enthält weder 'symbols' noch 'symbol'.")


def _deep_merge(base: dict, override: dict) -> dict:
    """Tiefe Verschmelzung: override-Werte gewinnen; verschachtelte Dicts rekursiv."""
    result = copy.deepcopy(base)
    for key, value in override.items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def resolve_config(config: dict, symbol: str) -> dict:
    """
    Effektives Config für EIN Symbol: globales Config + optionaler
    symbol_overrides[symbol]-Block (tief gemischt). Mutiert das Original nicht.
    """
    overrides = config.get("symbol_overrides", {}) or {}
    symbol_override: dict[str, Any] = overrides.get(symbol, {})
    if not symbol_override:
        return copy.deepcopy(config)
    return _deep_merge(config, symbol_override)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python scripts/test_config_utils.py`
Expected: PASS — `5 Tests: 5 bestanden, 0 fehlgeschlagen.`

- [ ] **Step 5: Commit**

```bash
git add src/config_utils.py scripts/test_config_utils.py
git commit -m "feat: config_utils fuer Multi-Coin (Symbol-Liste + Per-Coin-Overrides)"
```

---

## Task 2: config.yaml auf Multi-Coin umstellen

**Files:**
- Modify: `config/config.yaml`

- [ ] **Step 1: `market`-Block ersetzen**

Ersetze den bestehenden `market`-Block:

```yaml
market:
  symbol: "BTC/USDT"          # BTC/USDT Perpetual Futures (BingX)
  timeframe: "5m"             # Spec: 5 Minuten
```

durch:

```yaml
market:
  timeframe: "5m"             # Spec: 5 Minuten
  symbols:                    # Multi-Coin: alle BingX-Perpetuals, 24/7
    - "BTC/USDT"
    - "ETH/USDT"
    - "SOL/USDT"
    - "DOGE/USDT"
    - "BNB/USDT"
    - "ADA/USDT"

# Optionale Overrides pro Coin (anfangs leer → globale Defaults gelten).
# Beispiel (auskommentiert) dokumentiert nur die Struktur:
symbol_overrides: {}
  # "SOL/USDT":
  #   stop_loss:
  #     buffer_pct: 0.5
  #   anchor_trigger:
  #     trigger_min_abs_wt1: 55
```

- [ ] **Step 2: `risk`-Block anpassen**

Im `risk`-Block ändern (RR bleibt 2:1, NICHT anfassen):

```yaml
risk:
  risk_per_trade_pct: 1.0
  leverage: 3
  max_open_positions: 6       # GEAENDERT: kein echtes Parallel-Limit (= Anzahl Coins)
  max_daily_loss_pct: 10.0    # GEAENDERT (war 3.0)
```

- [ ] **Step 3: `paper_trading`-Block anpassen**

```yaml
paper_trading:
  start_balance: 1000.0       # EINE geteilte Balance ueber alle Coins
  risk_pct: 1.0
  leverage_cap: 3.0
  max_daily_loss_pct: 10.0    # GEAENDERT (war 3.0)
```

- [ ] **Step 4: Verify — Config lädt + Symbole stimmen**

Run:
```bash
python -c "import sys; sys.path.insert(0,'.'); import yaml; from src.config_utils import get_symbols; c=yaml.safe_load(open('config/config.yaml',encoding='utf-8')); print(get_symbols(c)); print('daily', c['paper_trading']['max_daily_loss_pct'], 'rr', c['take_profit']['rr_standard'])"
```
Expected: `['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'DOGE/USDT', 'BNB/USDT', 'ADA/USDT']` und `daily 10.0 rr 2.0`

- [ ] **Step 5: Commit**

```bash
git add config/config.yaml
git commit -m "feat: config.yaml auf 6 Coins, Tageslimit 10%, RR bleibt 2:1"
```

---

## Task 3: Journal — Multi-Coin-State + symbol in trades.csv + Migration

**Files:**
- Modify: `src/paper/journal.py`
- Test: `scripts/test_journal_multicoin.py` (Create)

- [ ] **Step 1: Write the failing tests**

```python
# scripts/test_journal_multicoin.py
"""
Tests fuer das Multi-Coin-Journal (positions-Dict, symbol in trades.csv, Migration).
Direkt ausfuehrbar: python scripts/test_journal_multicoin.py
"""
import json
import sys
import tempfile
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.paper.journal import Journal
from src.paper.position import Position

_pass = 0
_fail = 0


def _assert(cond, label):
    global _pass, _fail
    if cond:
        _pass += 1
        print(f"  ✓  {label}")
    else:
        _fail += 1
        print(f"  ✗  FEHLER: {label}")


_CONFIG = {
    "market": {"symbols": ["BTC/USDT", "ETH/USDT"], "timeframe": "5m"},
    "paper_trading": {"start_balance": 1000.0},
}


def _pos():
    return Position(entry=100.0, sl=99.0, tp1=102.0, qty=1.0,
                    direction="long", entry_time=pd.Timestamp("2026-01-01T10:00:00Z"),
                    divergence=False)


def test_fresh_state_has_positions_dict():
    with tempfile.TemporaryDirectory() as tmp:
        j = Journal(_CONFIG, data_dir=Path(tmp))
        _assert(j.state["balance"] == 1000.0, "1. fresh: balance=1000")
        _assert(j.state["positions"] == {}, "1. fresh: leeres positions-Dict")
        _assert(j.get_position("BTC/USDT") is None, "1. get_position: flach=None")


def test_set_and_persist_position_per_symbol():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        j1 = Journal(_CONFIG, data_dir=tmp_path)
        j1.set_position("ETH/USDT", _pos())
        j1.save_state()

        j2 = Journal(_CONFIG, data_dir=tmp_path)
        eth = j2.get_position("ETH/USDT")
        _assert(eth is not None and eth.direction == "long",
                "2. Position pro Symbol bleibt ueber Neustart erhalten")
        _assert(j2.get_position("BTC/USDT") is None,
                "2. anderes Symbol bleibt flach")


def test_last_candle_time_per_symbol():
    with tempfile.TemporaryDirectory() as tmp:
        j = Journal(_CONFIG, data_dir=Path(tmp))
        j.set_last_candle_time("BTC/USDT", "2026-01-01T10:00:00+00:00")
        _assert(j.get_last_candle_time("BTC/USDT") == "2026-01-01T10:00:00+00:00",
                "3. last_candle_time pro Symbol gesetzt/gelesen")
        _assert(j.get_last_candle_time("ETH/USDT") is None,
                "3. last_candle_time anderes Symbol = None")


def test_record_trade_writes_symbol_column():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        j = Journal(_CONFIG, data_dir=tmp_path)
        j.record_trade(symbol="ETH/USDT", position=_pos(),
                       exit_time=pd.Timestamp("2026-01-01T10:30:00Z"),
                       exit_price=102.0, exit_reason="tp1", balance_after=1002.0)
        rows = (tmp_path / "trades.csv").read_text(encoding="utf-8").splitlines()
        _assert(rows[0].startswith("symbol,"), "4. trades.csv: symbol ist erste Spalte")
        _assert(rows[1].startswith("ETH/USDT,"), "4. trades.csv: Zeile beginnt mit Symbol")


def test_migration_v1_to_v2():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        # Altes v1-state.json (single open_position, single last_candle_time)
        old_state = {
            "balance": 1234.0,
            "open_position": _pos().to_dict(),
            "day_start_balance": 1234.0,
            "day": "2026-01-01",
            "realized_pnl_today": -5.0,
            "last_candle_time": "2026-01-01T10:00:00+00:00",
        }
        (tmp_path / "state.json").write_text(json.dumps(old_state), encoding="utf-8")

        j = Journal(_CONFIG, data_dir=tmp_path)
        _assert(j.state["balance"] == 1234.0, "5. Migration: balance uebernommen")
        _assert("open_position" not in j.state, "5. Migration: altes open_position entfernt")
        btc = j.get_position("BTC/USDT")
        _assert(btc is not None and btc.entry == 100.0,
                "5. Migration: alte Position -> positions['BTC/USDT']")
        _assert(j.get_last_candle_time("BTC/USDT") == "2026-01-01T10:00:00+00:00",
                "5. Migration: last_candle_time -> BTC/USDT")
        _assert((tmp_path / "state.json.bak").exists(),
                "5. Migration: Backup state.json.bak angelegt")


def main():
    print("Journal-Multi-Coin-Tests\n")
    test_fresh_state_has_positions_dict()
    test_set_and_persist_position_per_symbol()
    test_last_candle_time_per_symbol()
    test_record_trade_writes_symbol_column()
    test_migration_v1_to_v2()
    print(f"\n{_pass + _fail} Tests: {_pass} bestanden, {_fail} fehlgeschlagen.")
    if _fail > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python scripts/test_journal_multicoin.py`
Expected: FAIL — `AttributeError`/`KeyError` (z.B. `get_position` existiert nicht, `positions` fehlt im State).

- [ ] **Step 3: Journal umbauen**

Ersetze in `src/paper/journal.py` die `_CSV_COLUMNS`-Konstante (symbol als erste Spalte):

```python
_CSV_COLUMNS = [
    "symbol",
    "entry_time", "exit_time", "direction",
    "entry", "sl", "tp1",
    "exit_price", "exit_reason",
    "qty", "risk_pct", "rr",
    "pnl_usd", "pnl_pct", "balance_after",
    "divergence",
]

# State-Schema-Version (v2 = Multi-Coin). v1 = altes single-symbol-Schema.
_STATE_VERSION = 2
```

Ersetze die Methode `_load_state` vollständig durch:

```python
    def _load_state(self) -> dict:
        """
        Liest state.json oder legt frischen v2-Zustand an.
        Migriert ein altes v1-state.json (single open_position) automatisch
        nach v2 (positions-Dict). Vor der Migration wird ein Backup geschrieben.
        """
        if not self._state_path.exists():
            return self._fresh_state()

        with open(self._state_path, encoding="utf-8") as f:
            raw = json.load(f)

        # v1 erkennen: hat 'open_position' (single) statt 'positions' (dict).
        if "positions" not in raw:
            raw = self._migrate_v1_to_v2(raw)
            return raw

        # v2: Positionen rekonstruieren.
        positions = {}
        for symbol, pdict in (raw.get("positions") or {}).items():
            positions[symbol] = Position.from_dict(pdict) if pdict else None
        raw["positions"] = positions
        raw.setdefault("last_candle_time", {})
        return raw

    def _fresh_state(self) -> dict:
        """Frischer v2-Zustand mit geteilter Start-Balance."""
        return {
            "version": _STATE_VERSION,
            "balance": self.start_balance,
            "positions": {},            # symbol -> Position | nicht vorhanden = flach
            "day_start_balance": self.start_balance,
            "day": "",
            "realized_pnl_today": 0.0,
            "last_candle_time": {},     # symbol -> ISO-String der letzten Kerze
        }

    def _migrate_v1_to_v2(self, raw: dict) -> dict:
        """
        Wandelt altes single-symbol-State in v2 um. Backup vorher.
        Die alte Position wandert nach positions['BTC/USDT'] (Altsystem war BTC).
        """
        backup = self._state_path.with_suffix(".json.bak")
        with open(backup, "w", encoding="utf-8") as f:
            json.dump(raw, f, indent=2)

        old_pos = raw.get("open_position")
        old_lct = raw.get("last_candle_time")

        state = {
            "version": _STATE_VERSION,
            "balance": float(raw.get("balance", self.start_balance)),
            "positions": {
                "BTC/USDT": Position.from_dict(old_pos) if old_pos else None
            },
            "day_start_balance": float(
                raw.get("day_start_balance", raw.get("balance", self.start_balance))
            ),
            "day": raw.get("day", ""),
            "realized_pnl_today": float(raw.get("realized_pnl_today", 0.0)),
            "last_candle_time": {"BTC/USDT": old_lct} if old_lct else {},
        }
        # leere Positionen entfernen (None bleibt erlaubt für gesetzte, aber flache)
        if state["positions"].get("BTC/USDT") is None:
            state["positions"] = {}
        return state
```

Ersetze die Methode `save_state` durch (positions-Dict serialisieren):

```python
    def save_state(self) -> None:
        """Schreibt den aktuellen v2-Zustand in state.json."""
        raw = dict(self.state)
        raw["positions"] = {
            symbol: (pos.to_dict() if isinstance(pos, Position) else None)
            for symbol, pos in self.state.get("positions", {}).items()
        }
        with open(self._state_path, "w", encoding="utf-8") as f:
            json.dump(raw, f, indent=2, default=str)
```

Füge nach `save_state` diese Zugriffs-Helfer ein:

```python
    # ------------------------------------------------------------------
    # Per-Symbol-Zugriff
    # ------------------------------------------------------------------

    def get_position(self, symbol: str):
        """Offene Position fuer ein Symbol oder None (flach)."""
        return self.state.get("positions", {}).get(symbol)

    def set_position(self, symbol: str, position) -> None:
        """Setzt (oder loescht via None) die Position fuer ein Symbol."""
        self.state.setdefault("positions", {})
        if position is None:
            self.state["positions"].pop(symbol, None)
        else:
            self.state["positions"][symbol] = position

    def get_last_candle_time(self, symbol: str):
        """ISO-String der zuletzt verarbeiteten Kerze fuer ein Symbol oder None."""
        return self.state.get("last_candle_time", {}).get(symbol)

    def set_last_candle_time(self, symbol: str, iso_ts: str) -> None:
        self.state.setdefault("last_candle_time", {})
        self.state["last_candle_time"][symbol] = iso_ts
```

Ändere die Signatur von `record_trade` — `symbol` als erstes Argument — und füge `symbol` in die `row` ein:

```python
    def record_trade(
        self,
        symbol: str,
        position: Position,
        exit_time: pd.Timestamp,
        exit_price: float,
        exit_reason: str,
        balance_after: float,
    ) -> None:
```

und im `row`-Dict als erstes Feld:

```python
        row = {
            "symbol": symbol,
            "entry_time": position.entry_time.strftime("%Y-%m-%d %H:%M"),
            # ... Rest unveraendert ...
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python scripts/test_journal_multicoin.py`
Expected: PASS — `5 Tests: 5 bestanden, 0 fehlgeschlagen.`

- [ ] **Step 5: Commit**

```bash
git add src/paper/journal.py scripts/test_journal_multicoin.py
git commit -m "feat: Journal Multi-Coin (positions-Dict, symbol-Spalte, v1->v2-Migration)"
```

---

## Task 4: paper_engine — symbol-bewusste Exit/Entry-Logik + run_cycle

**Files:**
- Modify: `src/paper/paper_engine.py`
- Test: `scripts/test_paper_engine.py` (bestehende Tests anpassen + neue)

> **Hintergrund:** `check_exit` und `size_qty` bleiben unveraendert. Die alte
> `_make_decision(df, setups, journal, config)` wird durch eine symbol-bewusste
> Variante ersetzt; `run_cycle` orchestriert die zwei Phasen ueber alle Coins.

- [ ] **Step 1: Bestehende Tests auf Multi-Coin-API umstellen (failing machen)**

In `scripts/test_paper_engine.py`:

Import-Zeile ändern:
```python
from src.paper.paper_engine import _decide_symbol, check_exit, run_cycle, size_qty
```

`_BASE_CONFIG` auf Multi-Coin-Schema bringen:
```python
_BASE_CONFIG = {
    "market": {"symbols": ["BTC/USDT"], "timeframe": "5m"},
    "paper_trading": {
        "start_balance": 1000.0,
        "risk_pct": 1.0,
        "leverage_cap": 3.0,
        "max_daily_loss_pct": 3.0,
    },
}
```

Alle Aufrufe `_make_decision(df, setups, j, cfg)` ersetzen durch
`_decide_symbol("BTC/USDT", df, setups, j, cfg, gate_open=True)`,
und State-Zugriffe von `j.state["open_position"]` auf `j.get_position("BTC/USDT")` umstellen.
(Tests 6/7 Fresh/Persist: `j.state["open_position"]` → `j.get_position("BTC/USDT")`.)

Neue Multi-Coin-Tests ans Ende (vor `main`) anfügen:

```python
# --- 13. run_cycle: zwei Coins, Exit BTC + Entry ETH im selben Zyklus ---
def test_run_cycle_two_coins():
    with tempfile.TemporaryDirectory() as tmp:
        cfg = {
            "market": {"symbols": ["BTC/USDT", "ETH/USDT"], "timeframe": "5m"},
            "paper_trading": {"start_balance": 1000.0, "risk_pct": 1.0,
                              "leverage_cap": 3.0, "max_daily_loss_pct": 10.0},
        }
        j = _make_journal(cfg, Path(tmp))

        # BTC hat offene Long-Position, die per TP schliesst.
        j.set_position("BTC/USDT", Position(entry=100.0, sl=99.0, tp1=102.0, qty=1.0,
                       direction="long", entry_time=_ts(-5), divergence=False))
        last = _ts(0)

        # Vorberechnete Per-Coin-Daten (Test-Hook, kein Netzwerk):
        per_symbol = {
            "BTC/USDT": (_make_df([_candle(last, close=101.5, high=102.5, low=100.5)]),
                         pd.DataFrame()),                       # kein neues Setup
            "ETH/USDT": (_make_df([_candle(last, close=100.0)]),
                         _make_setup(last, direction="long", entry=100.0, sl=99.0, tp1=102.0)),
        }
        result = run_cycle(j, cfg, _per_symbol=per_symbol)

        _assert(j.get_position("BTC/USDT") is None, "13. BTC per TP geschlossen")
        _assert(j.get_position("ETH/USDT") is not None, "13. ETH-Entry genommen")
        _assert(result["BTC/USDT"]["exit_reason"] == "tp1", "13. BTC exit_reason=tp1")
        _assert(result["ETH/USDT"]["entry_taken"], "13. ETH entry_taken=True")


# --- 14. run_cycle: kein Parallel-Limit — beide Coins gleichzeitig offen ---
def test_run_cycle_no_parallel_limit():
    with tempfile.TemporaryDirectory() as tmp:
        cfg = {
            "market": {"symbols": ["BTC/USDT", "ETH/USDT"], "timeframe": "5m"},
            "paper_trading": {"start_balance": 1000.0, "risk_pct": 1.0,
                              "leverage_cap": 3.0, "max_daily_loss_pct": 10.0},
        }
        j = _make_journal(cfg, Path(tmp))
        last = _ts(0)
        per_symbol = {
            "BTC/USDT": (_make_df([_candle(last, close=100.0)]),
                         _make_setup(last, "long", 100.0, 99.0, 102.0)),
            "ETH/USDT": (_make_df([_candle(last, close=100.0)]),
                         _make_setup(last, "long", 100.0, 99.0, 102.0)),
        }
        run_cycle(j, cfg, _per_symbol=per_symbol)
        _assert(j.get_position("BTC/USDT") is not None
                and j.get_position("ETH/USDT") is not None,
                "14. Beide Coins gleichzeitig offen (kein Parallel-Limit)")


# --- 15. run_cycle: geteiltes Tageslimit pausiert ALLE Entries ---
def test_run_cycle_shared_daily_gate():
    with tempfile.TemporaryDirectory() as tmp:
        cfg = {
            "market": {"symbols": ["BTC/USDT", "ETH/USDT"], "timeframe": "5m"},
            "paper_trading": {"start_balance": 1000.0, "risk_pct": 1.0,
                              "leverage_cap": 3.0, "max_daily_loss_pct": 10.0},
        }
        j = _make_journal(cfg, Path(tmp))
        today = pd.Timestamp.now(tz="utc").strftime("%Y-%m-%d")
        j.state["day"] = today
        j.state["day_start_balance"] = 1000.0
        j.state["realized_pnl_today"] = -100.0   # -10% exakt → Gate aktiv
        last = _ts(0)
        per_symbol = {
            "BTC/USDT": (_make_df([_candle(last)]), _make_setup(last)),
            "ETH/USDT": (_make_df([_candle(last)]), _make_setup(last)),
        }
        run_cycle(j, cfg, _per_symbol=per_symbol)
        _assert(j.get_position("BTC/USDT") is None
                and j.get_position("ETH/USDT") is None,
                "15. Geteiltes Tageslimit -10% pausiert alle Entries")
```

Und in `main()` die drei neuen Tests aufrufen:
```python
    test_run_cycle_two_coins()
    test_run_cycle_no_parallel_limit()
    test_run_cycle_shared_daily_gate()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python scripts/test_paper_engine.py`
Expected: FAIL — `ImportError: cannot import name '_decide_symbol'` / `run_cycle`.

- [ ] **Step 3: Engine umbauen**

In `src/paper/paper_engine.py`: Importe ergänzen:
```python
from src.config_utils import get_symbols, resolve_config
```

`check_exit` und `size_qty` **unverändert lassen**. Ersetze die alte `_make_decision`
durch die folgenden drei Funktionen:

```python
def _rollover_if_new_day(state: dict) -> None:
    """Setzt Tageswerte zurueck, wenn ein neuer UTC-Tag begonnen hat (geteilt)."""
    today_utc = pd.Timestamp.now(tz="utc").strftime("%Y-%m-%d")
    if state.get("day", "") != today_utc:
        state["day"] = today_utc
        state["day_start_balance"] = state["balance"]
        state["realized_pnl_today"] = 0.0


def _gate_open(state: dict, config: dict) -> bool:
    """True, wenn das geteilte Tagesverlust-Limit NEUE Entries noch zulaesst."""
    pt_cfg = config.get("paper_trading", {})
    max_loss_pct = float(pt_cfg.get("max_daily_loss_pct", 10.0))
    day_start = state.get("day_start_balance", state["balance"])
    pnl_today = state.get("realized_pnl_today", 0.0)
    daily_loss_pct = (pnl_today / day_start * 100) if day_start > 0 else 0.0
    return daily_loss_pct > -max_loss_pct


def _decide_symbol(
    symbol: str,
    df: pd.DataFrame,
    setups: pd.DataFrame,
    journal: Journal,
    config: dict,
    gate_open: bool,
) -> dict:
    """
    Exit-Pruefung + Entry-Entscheidung fuer EIN Symbol gegen die GETEILTE Balance.
    Mutiert journal.state (Balance, positions[symbol]). Speichert NICHT (das macht run_cycle).

    Dedup pro Symbol ueber journal.last_candle_time[symbol]. Entry nur wenn
    gate_open (geteiltes Tageslimit) UND Symbol flach UND gueltiges Setup auf
    der letzten Kerze. Kein Parallel-Limit (jeder flache Coin darf rein).
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

    # Dedup pro Symbol
    last_processed = journal.get_last_candle_time(symbol)
    if last_processed is not None:
        lp = pd.Timestamp(last_processed)
        lt = last_ts
        if lp.tzinfo is None and lt.tzinfo is not None:
            lp = lp.tz_localize("utc")
        elif lp.tzinfo is not None and lt.tzinfo is None:
            lt = lt.tz_localize("utc")
        if lp >= lt:
            return result

    # --- 1. EXIT ZUERST ---
    position = journal.get_position(symbol)
    if position is not None:
        reason, exit_price = check_exit(position, last_candle)
        if reason is not None:
            pnl_usd = position.pnl(exit_price)
            state["balance"] += pnl_usd
            state["realized_pnl_today"] = state.get("realized_pnl_today", 0.0) + pnl_usd
            journal.set_position(symbol, None)
            journal.record_trade(
                symbol=symbol, position=position, exit_time=last_ts,
                exit_price=exit_price, exit_reason=reason,
                balance_after=state["balance"],
            )
            result["exit_reason"] = reason
            result["exit_price"] = exit_price

    # --- 2. ENTRY (nur wenn flach, gate offen) ---
    if gate_open and journal.get_position(symbol) is None and not setups.empty:
        pt_cfg = config.get("paper_trading", {})
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
        if not valid.empty:
            result["setup_found"] = True
            s = valid.iloc[-1]
            entry, sl, tp1 = float(s["entry"]), float(s["sl"]), float(s["tp1"])
            qty = size_qty(state["balance"], entry, sl, pt_cfg)
            if qty > 0:
                journal.set_position(symbol, Position(
                    entry=entry, sl=sl, tp1=tp1, qty=qty,
                    direction=s["direction"], entry_time=last_ts,
                    divergence=bool(s.get("divergence_active", False)),
                ))
                result["entry_taken"] = True

    journal.set_last_candle_time(symbol, last_ts.isoformat())
    return result
```

Ersetze schließlich `run_once` durch `run_cycle` (zwei-Phasen-Orchestrierung):

```python
def _load_symbol_data(symbol: str, config: dict):
    """Laedt BingX-Kerzen fuer EIN Symbol und berechnet alle Indikatoren + Setups."""
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
    Ein vollstaendiger Multi-Coin-Zyklus.

    Phase 0: Tages-Rollover (geteilt).
    Phase 1: Daten je Coin laden, alle EXITS buchen (Balance aktualisieren).
    Phase 2: geteiltes Tageslimit-Gate, dann ENTRIES je flachem Coin.

    _per_symbol (Test-Hook): {symbol: (df, setups)} ueberspringt das Laden.
    Rueckgabe: {symbol: result-dict}.
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

    # PHASE 1 — EXITS zuerst (Balance wird aktualisiert), Setups hier ignorieren.
    results = {}
    for symbol, (df, _setups) in data.items():
        results[symbol] = _decide_symbol(
            symbol, df, pd.DataFrame(), journal, config, gate_open=False
        )

    # PHASE 2 — ENTRIES gegen aktualisierte Balance, geteiltes Gate.
    gate = _gate_open(journal.state, config)
    for symbol, (df, setups) in data.items():
        entry_res = _decide_symbol(symbol, df, setups, journal, config, gate_open=gate)
        # Exit-Felder aus Phase 1 erhalten, Entry-Felder ergaenzen.
        results[symbol]["entry_taken"] = entry_res["entry_taken"]
        results[symbol]["setup_found"] = entry_res["setup_found"]

    journal.save_state()
    return results
```

> **Dedup-Hinweis:** `_decide_symbol` setzt `last_candle_time[symbol]` schon in Phase 1.
> Damit Phase 2 nicht durch Dedup blockiert, MUSS Phase 2 vor dem Dedup-Check stehen?
> Nein — Loesung: Dedup nur in Phase 1 anwenden. Setze in Phase 1 `last_candle_time`
> NICHT, sondern erst in Phase 2. Konkret: entferne die `journal.set_last_candle_time`-Zeile
> aus `_decide_symbol` und rufe sie in `run_cycle` NUR in der Phase-2-Schleife auf,
> NACHDEM `_decide_symbol` lief. Passe `_decide_symbol` an: Dedup-Check bleibt, aber das
> Setzen entfaellt dort.

Konkrete Korrektur — in `_decide_symbol` die Zeile
`journal.set_last_candle_time(symbol, last_ts.isoformat())` **entfernen**, und in
`run_cycle` Phase 2 nach dem `_decide_symbol`-Aufruf ergänzen:

```python
        journal.set_last_candle_time(symbol, df.iloc[-1]["timestamp"].isoformat())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python scripts/test_paper_engine.py`
Expected: PASS — alle bisherigen + 3 neue (`test_run_cycle_*`) bestanden.

- [ ] **Step 5: Commit**

```bash
git add src/paper/paper_engine.py scripts/test_paper_engine.py
git commit -m "feat: paper_engine run_cycle (zwei-Phasen Multi-Coin, geteilte Balance)"
```

---

## Task 5: run_paper_loop — Loop ruft run_cycle über alle Coins

**Files:**
- Modify: `scripts/run_paper_loop.py`

- [ ] **Step 1: Import + Symbol-Liste**

Import ändern:
```python
from src.paper.paper_engine import run_cycle
from src.config_utils import get_symbols
```

- [ ] **Step 2: Start-Banner auf Multi-Coin**

Ersetze den Block ab `journal = Journal(config)` bis zum `print("=" * 60)` nach der Positions-Ausgabe durch:

```python
    journal = Journal(config)
    symbols = get_symbols(config)
    balance = journal.state["balance"]

    print("=" * 60)
    print("  Mick Trading Bot — Paper-Trading-Loop (Multi-Coin)")
    print("  KEINE ECHTEN ORDERS. Reine Simulation.")
    print("=" * 60)
    print(f"  Balance (geteilt): {balance:.2f} USDT")
    print(f"  Coins:             {', '.join(symbols)}")
    open_now = [s for s in symbols if journal.get_position(s) is not None]
    print(f"  Offene Positionen: {len(open_now)} ({', '.join(open_now) or 'keine'})")
    print("  Beenden: Ctrl+C")
    print("=" * 60)
```

- [ ] **Step 3: Loop-Körper auf run_cycle umstellen**

Ersetze den `try`-Block-Inhalt (ab `result = run_once(...)` bis zur letzten Coin-Ausgabe)
durch:

```python
            try:
                results = run_cycle(journal, config)
            except Exception as exc:
                print(f"  ❌ Fehler im Zyklus: {exc}", flush=True)
                continue

            for symbol in get_symbols(config):
                r = results.get(symbol)
                if r is None:
                    print(f"  {symbol}: (uebersprungen)", flush=True)
                    continue
                line = f"  {symbol}: "
                if r["exit_reason"]:
                    line += f"EXIT {r['exit_reason'].upper()} @{r['exit_price']:.4f} | "
                if r["entry_taken"]:
                    p = journal.get_position(symbol)
                    line += f"ENTRY {p.direction.upper()} @{p.entry:.4f} | "
                pos = journal.get_position(symbol)
                line += f"Pos: {pos.direction.upper()+'@'+format(pos.entry,'.4f') if pos else 'flach'}"
                print(line, flush=True)

            print(f"  Balance: {journal.state['balance']:.2f} USDT", flush=True)
```

- [ ] **Step 4: Verify — Loop startet sauber (ein Zyklus, dann Ctrl+C)**

Run (kurz laufen lassen, dann Ctrl+C): `python scripts/run_paper_loop.py`
Expected: Banner zeigt 6 Coins + geteilte Balance; nach erstem Zyklus eine Zeile pro Coin (live BingX-Daten). Keine Exceptions.

- [ ] **Step 5: Commit**

```bash
git add scripts/run_paper_loop.py
git commit -m "feat: run_paper_loop auf Multi-Coin-run_cycle umgestellt"
```

---

## Task 6: Migrations-Skript für bestehende Daten

**Files:**
- Create: `scripts/migrate_state_v2.py`

> **Zweck:** Bestehende `data/state.json` (v1) + `data/trades.csv` (ohne symbol-Spalte)
> einmalig auf das neue Schema heben. Der Journal-Loader migriert state.json bereits beim
> Start (Task 3); dieses Skript ist der explizite, geloggte Einmal-Lauf inkl. trades.csv
> und zeigt dem User, was passiert ist.

- [ ] **Step 1: Skript schreiben**

```python
# scripts/migrate_state_v2.py
"""
Einmalige Migration der Paper-Trading-Daten auf das Multi-Coin-Schema (v2).

- state.json: v1 (single open_position) -> v2 (positions-Dict). Backup: state.json.bak
- trades.csv: ergaenzt die fehlende erste Spalte 'symbol' mit 'BTC/USDT'.
              Backup: trades.csv.bak

Idempotent: bereits migrierte Dateien werden erkannt und nicht doppelt angefasst.
Aufruf: python scripts/migrate_state_v2.py
"""
import csv
import json
import shutil
import sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"

_NEW_HEADER = [
    "symbol", "entry_time", "exit_time", "direction", "entry", "sl", "tp1",
    "exit_price", "exit_reason", "qty", "risk_pct", "rr", "pnl_usd", "pnl_pct",
    "balance_after", "divergence",
]


def migrate_state(path: Path) -> None:
    if not path.exists():
        print(f"  state.json nicht vorhanden ({path}) — uebersprungen.")
        return
    raw = json.loads(path.read_text(encoding="utf-8"))
    if "positions" in raw:
        print("  state.json ist bereits v2 — uebersprungen.")
        return
    shutil.copy2(path, path.with_suffix(".json.bak"))
    old_pos = raw.get("open_position")
    old_lct = raw.get("last_candle_time")
    new = {
        "version": 2,
        "balance": raw.get("balance", 1000.0),
        "positions": {"BTC/USDT": old_pos} if old_pos else {},
        "day_start_balance": raw.get("day_start_balance", raw.get("balance", 1000.0)),
        "day": raw.get("day", ""),
        "realized_pnl_today": raw.get("realized_pnl_today", 0.0),
        "last_candle_time": {"BTC/USDT": old_lct} if old_lct else {},
    }
    path.write_text(json.dumps(new, indent=2), encoding="utf-8")
    print("  state.json -> v2 migriert (Backup: state.json.bak).")


def migrate_trades(path: Path) -> None:
    if not path.exists():
        print(f"  trades.csv nicht vorhanden ({path}) — uebersprungen.")
        return
    rows = list(csv.reader(path.open(encoding="utf-8")))
    if not rows:
        print("  trades.csv leer — uebersprungen.")
        return
    if rows[0] and rows[0][0] == "symbol":
        print("  trades.csv hat bereits symbol-Spalte — uebersprungen.")
        return
    shutil.copy2(path, path.with_suffix(".csv.bak"))
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(_NEW_HEADER)
        for row in rows[1:]:   # alte Headerzeile verwerfen, Daten mit BTC/USDT praefixen
            w.writerow(["BTC/USDT", *row])
    print("  trades.csv -> symbol-Spalte ergaenzt (Backup: trades.csv.bak).")


def main() -> None:
    print("Migration auf Multi-Coin-Schema (v2)\n")
    migrate_state(DATA_DIR / "state.json")
    migrate_trades(DATA_DIR / "trades.csv")
    print("\nFertig.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify — Idempotenz auf Kopie testen**

Run:
```bash
python scripts/migrate_state_v2.py
python scripts/migrate_state_v2.py
```
Expected: Erster Lauf migriert (oder „nicht vorhanden"); zweiter Lauf meldet überall „bereits v2"/„hat bereits symbol-Spalte". Keine Exception.

- [ ] **Step 3: Commit**

```bash
git add scripts/migrate_state_v2.py
git commit -m "feat: einmaliges Migrations-Skript state/trades auf Multi-Coin-Schema"
```

---

## Task 7: Dashboard — 6-Coin-Übersicht

**Files:**
- Modify: `scripts/dashboard.html`

> **Zuerst lesen:** Das bestehende `scripts/dashboard.html` komplett lesen, um den
> aktuellen Datenabruf (fetch von `state.json`, `trades.csv`, `watchdog_status.json`),
> die Design-Tokens (Space Grotesk, Glow-Cards, M-Logo, Roboter, 0.85-Zoom, deutsche Zeit)
> und die Render-Funktionen zu verstehen. Design NICHT neu erfinden — nur erweitern.

- [ ] **Step 1: State-Parsing anpassen**

Das Dashboard liest `data/state.json`. Neue Struktur beachten:
- `state.balance` (geteilt) statt einzelner Balance — Anzeige als „Balance (geteilt)".
- `state.positions` ist jetzt ein Objekt `{ "BTC/USDT": {...}|fehlt, ... }`. Flach = Symbol
  fehlt im Objekt oder Wert `null`.
- `state.last_candle_time` ist ein Objekt pro Symbol (für „zuletzt aktualisiert" je Coin).

Symbol-Liste im Dashboard hart als Anzeige-Reihenfolge hinterlegen (passend zur config):
```js
const SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "DOGE/USDT", "BNB/USDT", "ADA/USDT"];
```

- [ ] **Step 2: Kopfzeile erweitern**

Im Header zusätzlich anzeigen:
- Geteilte Balance + Tages-PnL in % (`(balance - day_start_balance) / day_start_balance * 100`).
- Tageslimit-Status: „offen" wenn `realized_pnl_today / day_start_balance * 100 > -10`, sonst
  „PAUSIERT" (rot).
- Positionszähler: Anzahl Coins mit offener Position, Format „N / 6 offen".

- [ ] **Step 3: Coin-Tabelle rendern**

Statt einer einzelnen Positionskarte eine Tabelle/Kartenliste mit einer Zeile pro Symbol
aus `SYMBOLS`. Pro Zeile:
- Symbol (Kurzform, z.B. „BTC").
- Status: „flach" oder „LONG @<entry>" / „SHORT @<entry>" aus `state.positions[symbol]`.
- SL / TP1 (aus der Position, sonst „—").
- Floating-PnL: nur wenn offen — benötigt aktuellen Preis. Falls das Dashboard keinen
  Live-Preis hat, dieses Feld als „—" lassen (kein neuer Netzwerkpfad im Dashboard;
  Floating-PnL ist optional und kann in einer Folgeiteration ergänzt werden).
- Letzter abgeschlossener Trade dieses Coins: aus `trades.csv` die jüngste Zeile mit
  `symbol == <symbol>` (Spalte `symbol` ist jetzt vorhanden), zeige `exit_reason` + `pnl_pct`.

- [ ] **Step 4: Trade-Historie um Symbol-Spalte erweitern**

Die bestehende Trade-Historie-Tabelle um eine erste Spalte „Coin" (Wert aus `symbol`)
ergänzen. Optional: einfacher Filter-Dropdown „alle Coins / einzelner Coin".

- [ ] **Step 5: Verify — Dashboard zeigt 6 Coins**

Run (HTTP-Server muss laufen):
```bash
python -m http.server 8080
```
Im Browser `http://localhost:8080/scripts/dashboard.html` öffnen.
Expected: Header zeigt geteilte Balance + „N / 6 offen" + Tageslimit-Status; Coin-Tabelle
listet alle 6 Coins mit korrektem Flach/Offen-Status; Trade-Historie hat Coin-Spalte;
keine JS-Konsolenfehler.

- [ ] **Step 6: Commit**

```bash
git add scripts/dashboard.html
git commit -m "feat: Dashboard 6-Coin-Uebersicht (geteilte Balance, Positionszaehler, Coin-Spalte)"
```

---

## Task 8: End-to-End-Verifikation + Session-Status pflegen

**Files:**
- Modify: `session-status.md`

- [ ] **Step 1: Alle Test-Suites laufen lassen**

Run:
```bash
python scripts/test_config_utils.py
python scripts/test_journal_multicoin.py
python scripts/test_paper_engine.py
```
Expected: alle drei „0 fehlgeschlagen".

- [ ] **Step 2: Migration auf echten Daten (mit Backup-Kontrolle)**

Run: `python scripts/migrate_state_v2.py`
Danach prüfen: `data/state.json` hat `positions`, `data/state.json.bak` existiert,
`data/trades.csv` erste Spalte = `symbol`, `data/trades.csv.bak` existiert.

- [ ] **Step 3: Ein Live-Zyklus gegen BingX**

Run (kurz, dann Ctrl+C): `python scripts/run_paper_loop.py`
Expected: 6 Coins werden geladen, je Coin eine Statuszeile, keine Exceptions, geteilte
Balance unverändert (sofern kein Exit/Entry).

- [ ] **Step 4: session-status.md aktualisieren**

Im Abschnitt „Aktueller Stand" die Coins von „BTC/USDT" auf „6 Coins (BTC, ETH, SOL, DOGE,
BNB, ADA), gemeinsames Konto" ändern. „Offene Schritte" um beobachtende Kalibrierung
(Per-Coin-Overrides für SOL/DOGE) ergänzen. Erledigtes ins Archiv.

- [ ] **Step 5: Commit**

```bash
git add session-status.md
git commit -m "docs: session-status auf Multi-Coin-Stand aktualisiert"
```

---

## Self-Review-Ergebnis (vom Planer)

- **Spec-Abdeckung:** Symbol-Liste (T1/T2), Per-Coin-Overrides (T1/T2), geteilte Balance +
  positions-Dict (T3), symbol in trades.csv (T3), zwei-Phasen-run_cycle (T4), kein
  Parallel-Limit (T4/Test 14), geteiltes Tageslimit 10 % (T4/Test 15), RR 2:1 (T2, unverändert),
  Migration (T3+T6), Dashboard (T7). Alle Spec-Abschnitte haben eine Task.
- **Platzhalter:** keine — alle Code-Schritte enthalten vollständigen Code; Dashboard-Task
  arbeitet bewusst an bestehender HTML-Datei und verweist auf „zuerst lesen".
- **Typ-Konsistenz:** `get_position`/`set_position`/`get_last_candle_time`/`set_last_candle_time`
  (T3) werden in T4 genau so verwendet; `run_cycle(journal, config, _per_symbol=...)` und
  `_decide_symbol(symbol, df, setups, journal, config, gate_open)` konsistent zwischen Tests (T4
  Step 1) und Implementierung (T4 Step 3); `record_trade(symbol, position, ...)` konsistent T3↔T4.
```
