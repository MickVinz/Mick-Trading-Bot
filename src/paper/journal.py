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
    "entry_time", "exit_time", "direction",
    "entry", "sl", "tp1",
    "exit_price", "exit_reason",
    "qty", "risk_pct", "rr",
    "pnl_usd", "pnl_pct", "balance_after",
    "divergence",
]


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
        """Liest state.json oder legt einen frischen Zustand an."""
        if self._state_path.exists():
            with open(self._state_path, encoding="utf-8") as f:
                raw = json.load(f)
            # Position rekonstruieren, falls vorhanden
            if raw.get("open_position"):
                raw["open_position"] = Position.from_dict(raw["open_position"])
            return raw

        # Erster Start: frischer Zustand mit Start-Balance.
        return {
            "balance": self.start_balance,
            "open_position": None,
            "day_start_balance": self.start_balance,
            "day": "",                  # wird beim ersten run_once gesetzt
            "realized_pnl_today": 0.0,
            "last_candle_time": None,   # Dedup: letzte verarbeitete Kerze
        }

    def save_state(self) -> None:
        """Schreibt den aktuellen Zustand in state.json."""
        raw = dict(self.state)
        if isinstance(raw.get("open_position"), Position):
            raw["open_position"] = raw["open_position"].to_dict()
        with open(self._state_path, "w", encoding="utf-8") as f:
            json.dump(raw, f, indent=2, default=str)

    # ------------------------------------------------------------------
    # Trade-Protokoll
    # ------------------------------------------------------------------

    def record_trade(
        self,
        position: Position,
        exit_time: pd.Timestamp,
        exit_price: float,
        exit_reason: str,
        balance_after: float,
    ) -> None:
        """
        Hängt eine Zeile an trades.csv an.

        exit_reason : 'tp1' oder 'sl'
        balance_after: Balance NACH Anrechnung des PnL.
        """
        pnl_usd = position.pnl(exit_price)
        balance_before = balance_after - pnl_usd

        risk_usd = position.qty * abs(position.entry - position.sl)
        pnl_pct = pnl_usd / balance_before * 100 if balance_before != 0 else 0.0
        rr = pnl_usd / risk_usd if risk_usd > 0 else 0.0
        risk_pct = abs(position.entry - position.sl) / position.entry * 100

        row = {
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
            "balance_after": round(balance_after, 4),
            "divergence": position.divergence,
        }

        write_header = not self._trades_path.exists()
        with open(self._trades_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=_CSV_COLUMNS)
            if write_header:
                writer.writeheader()
            writer.writerow(row)
