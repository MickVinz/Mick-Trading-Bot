"""
Paper-Trading-Journal: CSV-Trades + JSON-State.

trades.csv  — eine Zeile pro abgeschlossenem Trade (append-only).
state.json  — laufender Zustand: Balance, offene Position, Tages-PnL.

Beim ersten Start (kein state.json): Balance = paper_trading.start_balance
aus config.yaml (Default: 1000 USDT).
Bei Neustart (state.json vorhanden): Balance und offene Position werden
fortgeführt — kein Reset.
"""

import csv
import json
from pathlib import Path
from typing import Optional

import pandas as pd

from src.paper.position import Position

# Laufzeit-Dateien liegen unter <projekt-root>/data/ (gitignored).
_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"

_CSV_COLUMNS = [
    "symbol",
    "entry_time", "exit_time", "direction",
    "entry", "sl", "tp1",
    "exit_price", "exit_reason",
    "qty", "risk_pct", "rr",
    "pnl_usd", "pnl_pct", "fees_usd", "balance_after",
    "divergence",
]

# State-Schema-Version (v2 = Multi-Coin). v1 = altes single-symbol-Schema.
_STATE_VERSION = 2


class Journal:
    """
    Verwaltet trades.csv und state.json für den Paper-Trading-Loop.

    Alle Methoden mutieren self.state in-memory; save_state() schreibt
    state.json. record_trade() hängt eine Zeile an trades.csv an.
    """

    def __init__(self, config: dict, data_dir: Optional[Path] = None):
        """
        Parameters
        ----------
        config   : geladene config.yaml als dict.
        data_dir : Überschreibt den Standard-Datenpfad (für Tests).
        """
        pt_cfg = config.get("paper_trading", {})
        self.start_balance = float(pt_cfg.get("start_balance", 1000.0))

        self._data_dir = data_dir or _DATA_DIR
        self._data_dir.mkdir(parents=True, exist_ok=True)

        self._trades_path = self._data_dir / "trades.csv"
        self._state_path = self._data_dir / "state.json"

        self.state = self._load_state()

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

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
            return self._migrate_v1_to_v2(raw)

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

        positions = {}
        if old_pos:
            positions["BTC/USDT"] = Position.from_dict(old_pos)

        return {
            "version": _STATE_VERSION,
            "balance": float(raw.get("balance", self.start_balance)),
            "positions": positions,
            "day_start_balance": float(
                raw.get("day_start_balance", raw.get("balance", self.start_balance))
            ),
            "day": raw.get("day", ""),
            "realized_pnl_today": float(raw.get("realized_pnl_today", 0.0)),
            "last_candle_time": {"BTC/USDT": old_lct} if old_lct else {},
        }

    def save_state(self) -> None:
        """Schreibt den aktuellen v2-Zustand in state.json."""
        raw = dict(self.state)
        raw["positions"] = {
            symbol: (pos.to_dict() if isinstance(pos, Position) else None)
            for symbol, pos in self.state.get("positions", {}).items()
        }
        with open(self._state_path, "w", encoding="utf-8") as f:
            json.dump(raw, f, indent=2, default=str)

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

    # ------------------------------------------------------------------
    # Trade-Protokoll
    # ------------------------------------------------------------------

    def record_trade(
        self,
        symbol: str,
        position: Position,
        exit_time: pd.Timestamp,
        exit_price: float,
        exit_reason: str,
        balance_after: float,
        pnl_usd: Optional[float] = None,
        fees_usd: float = 0.0,
    ) -> None:
        """
        Hängt eine Zeile an trades.csv an.

        exit_reason : 'tp1' oder 'sl'
        balance_after: Balance NACH Anrechnung des NETTO-PnL.
        pnl_usd : realisierter NETTO-PnL (nach Gebühren/Slippage). None →
                  Brutto aus position.pnl(exit_price) (Abwärtskompatibilität).
        fees_usd: angefallene Round-Trip-Kosten (Gebühren + Slippage).
        """
        if pnl_usd is None:
            pnl_usd = position.pnl(exit_price)
        balance_before = balance_after - pnl_usd

        risk_usd = position.qty * abs(position.entry - position.sl)
        pnl_pct = pnl_usd / balance_before * 100 if balance_before != 0 else 0.0
        rr = pnl_usd / risk_usd if risk_usd > 0 else 0.0
        risk_pct = abs(position.entry - position.sl) / position.entry * 100

        row = {
            "symbol": symbol,
            "entry_time": position.entry_time.strftime("%Y-%m-%d %H:%M"),
            "exit_time": exit_time.strftime("%Y-%m-%d %H:%M"),
            "direction": position.direction,
            "entry": round(position.entry, 2),
            "sl": round(position.sl, 2),
            "tp1": round(position.tp1, 2),
            "exit_price": round(exit_price, 2),
            "exit_reason": exit_reason,
            "qty": round(position.qty, 6),
            "risk_pct": round(risk_pct, 3),
            "rr": round(rr, 3),
            "pnl_usd": round(pnl_usd, 4),
            "pnl_pct": round(pnl_pct, 4),
            "fees_usd": round(fees_usd, 4),
            "balance_after": round(balance_after, 4),
            "divergence": position.divergence,
        }

        write_header = not self._trades_path.exists()
        with open(self._trades_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=_CSV_COLUMNS)
            if write_header:
                writer.writeheader()
            writer.writerow(row)
