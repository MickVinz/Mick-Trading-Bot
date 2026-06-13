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
