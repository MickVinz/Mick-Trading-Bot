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
