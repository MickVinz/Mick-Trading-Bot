"""
Datenklasse für eine offene Paper-Trading-Position.

Enthält alle für Entry, Exit und PnL-Berechnung nötigen Felder und
kann serialisiert/deserialisiert werden, damit state.json die laufende
Position über Neustarts hinweg enthält.
"""

from dataclasses import dataclass

import pandas as pd


@dataclass
class Position:
    """Eine offene simulierte Position (kein echtes Geld, kein echter Trade)."""

    entry: float        # Einstiegspreis (Schlusskurs der Trigger-Kerze)
    sl: float           # Stop-Loss-Preis
    tp1: float          # Take-Profit-Preis (RR 2:1, volle Position)
    qty: float          # Menge in BTC (aus Position-Sizing)
    direction: str      # 'long' oder 'short'
    entry_time: pd.Timestamp  # UTC-Zeitstempel der Einstiegskerze
    divergence: bool    # War zum Zeitpunkt des Entries eine Divergenz aktiv?

    def pnl(self, current_price: float) -> float:
        """
        Unrealisierter (oder realisierter) PnL in USDT zum angegebenen Preis.

        Long:  (current - entry) × qty
        Short: (entry - current) × qty
        """
        sign = 1 if self.direction == "long" else -1
        return sign * (current_price - self.entry) * self.qty

    def to_dict(self) -> dict:
        """Serialisierung für state.json (alle Typen JSON-kompatibel)."""
        return {
            "entry": self.entry,
            "sl": self.sl,
            "tp1": self.tp1,
            "qty": self.qty,
            "direction": self.direction,
            "entry_time": self.entry_time.isoformat(),
            "divergence": self.divergence,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Position":
        """Deserialisierung aus state.json."""
        return cls(
            entry=float(d["entry"]),
            sl=float(d["sl"]),
            tp1=float(d["tp1"]),
            qty=float(d["qty"]),
            direction=d["direction"],
            entry_time=pd.Timestamp(d["entry_time"]),
            divergence=bool(d["divergence"]),
        )
