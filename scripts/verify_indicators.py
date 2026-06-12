"""
Verifizierungsskript: WaveTrend (wt1/wt2 + Dots) und MFI gegen den
TradingView/MarketCypher-Chart abgleichen.

Was das Skript macht:
    1. Lädt historische 5m-BTC/USDT-Kerzen von der öffentlichen
       Binance-API (kein API-Key nötig).
       Alternativ: --csv pfad/zu/kerzen.csv als Eingabe.
    2. Berechnet wt1, wt2, Dot-Kreuzungen und MFI
       (Parameter aus config/config.yaml).
    3. Gibt eine Tabelle der letzten 50 Kerzen aus:
       timestamp | close | wt1 | wt2 | dot | mfi

Aufruf (aus dem Projektordner):
    python scripts/verify_indicators.py
    python scripts/verify_indicators.py --csv meine_kerzen.csv
    python scripts/verify_indicators.py --rows 30

CSV-Format (falls --csv genutzt wird), eine Kopfzeile mit:
    timestamp,open,high,low,close,volume
    timestamp als ISO-Datum ("2026-06-12 08:00") oder Unix-Millisekunden.
"""

import argparse
import sys
from pathlib import Path

# Windows-Konsole auf UTF-8 stellen, damit Umlaute ("grün") korrekt erscheinen
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

import pandas as pd
import requests
import yaml

# Projekt-Root in den Suchpfad aufnehmen, damit "src" importierbar ist,
# egal von wo das Skript gestartet wird.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.indicators.wavetrend import calculate_wavetrend, detect_dots
from src.indicators.mfi import calculate_mfi

CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"

# Öffentlicher Binance-Spot-Endpunkt (kein Key nötig).
# Hinweis: Spot-Kerzen weichen minimal von BingX-Perpetual-Kerzen ab —
# für den Indikator-Abgleich in TradingView daher BINANCE:BTCUSDT wählen.
BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"


def load_config() -> dict:
    """Liest config/config.yaml ein."""
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def fetch_binance_klines(symbol: str = "BTCUSDT", interval: str = "5m",
                         limit: int = 500,
                         drop_unclosed: bool = True) -> pd.DataFrame:
    """
    Holt die letzten `limit` Kerzen von der öffentlichen Binance-API.

    Wichtig: Wir laden bewusst deutlich mehr als 50 Kerzen, weil EMAs
    eine "Aufwärmphase" brauchen — die ersten Werte einer EMA sind
    ungenau, erst nach vielen Kerzen stimmen sie mit TradingView überein.

    REPAINT-SCHUTZ (Architektur-Regel, siehe Spec Abschnitt 9):
    Die letzte Kerze der Binance-Antwort ist fast immer die noch LAUFENDE
    Kerze. Indikatorwerte und Dot-Kreuzungen auf dieser Kerze können sich
    bis zum Kerzenschluss noch ändern ("Repainting") — ein Setup, das auf
    ihr erkannt wird, kann wieder verschwinden. Deshalb werden hier
    standardmäßig (drop_unclosed=True) alle Kerzen verworfen, deren
    offizielle Schlusszeit noch in der Zukunft liegt. Alle nachgelagerten
    Berechnungen (WaveTrend, MFI, Setup-Erkennung) arbeiten damit
    ausschließlich auf GESCHLOSSENEN Kerzen.
    drop_unclosed=False nur für reine Anzeige-/Debug-Zwecke verwenden.
    """
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    response = requests.get(BINANCE_KLINES_URL, params=params, timeout=15)
    response.raise_for_status()
    raw = response.json()

    df = pd.DataFrame(raw, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades",
        "taker_buy_base", "taker_buy_quote", "ignore",
    ])

    # Typen konvertieren
    df["timestamp"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df["close_time"] = pd.to_datetime(df["close_time"], unit="ms", utc=True)
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = df[col].astype(float)

    # Repaint-Schutz: noch nicht geschlossene Kerzen verwerfen
    if drop_unclosed:
        now = pd.Timestamp.now(tz="utc")
        df = df[df["close_time"] <= now]

    return df[["timestamp", "open", "high", "low", "close", "volume"]].reset_index(drop=True)


def load_csv(path: str) -> pd.DataFrame:
    """
    Lädt Kerzen aus einer CSV-Datei (Fallback ohne Internet).

    Repaint-Schutz-Hinweis: Bei CSV-Daten kann nicht geprüft werden, ob die
    letzte Kerze geschlossen ist — der Export muss ausschließlich
    abgeschlossene Kerzen enthalten (bei historischen Exporten der Normalfall).
    """
    df = pd.read_csv(path)

    required = {"timestamp", "open", "high", "low", "close", "volume"}
    missing = required - set(df.columns)
    if missing:
        sys.exit(f"FEHLER: CSV fehlen die Spalten: {', '.join(sorted(missing))}")

    # Timestamp flexibel parsen: Unix-Millisekunden oder ISO-Datum
    if pd.api.types.is_numeric_dtype(df["timestamp"]):
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    else:
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)

    for col in ("open", "high", "low", "close", "volume"):
        df[col] = df[col].astype(float)

    return df.sort_values("timestamp").reset_index(drop=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="WaveTrend + MFI berechnen und zum Chart-Abgleich ausgeben."
    )
    parser.add_argument("--csv", help="CSV-Datei statt Binance-API nutzen")
    parser.add_argument("--rows", type=int, default=50,
                        help="Anzahl der auszugebenden Kerzen (Default: 50)")
    parser.add_argument("--limit", type=int, default=500,
                        help="Anzahl der zu ladenden Kerzen (Default: 500)")
    args = parser.parse_args()

    config = load_config()
    wt_cfg = config["wavetrend"]
    mfi_cfg = config["mfi"]

    # ------------------------------------------------------------------
    # 1. Daten laden
    # ------------------------------------------------------------------
    if args.csv:
        print(f"Lade Kerzen aus CSV: {args.csv}")
        df = load_csv(args.csv)
    else:
        symbol = config["market"]["symbol"].replace("/", "")  # "BTC/USDT" -> "BTCUSDT"
        interval = config["market"]["timeframe"]
        print(f"Lade {args.limit} x {interval}-Kerzen für {symbol} von Binance ...")
        try:
            df = fetch_binance_klines(symbol, interval, args.limit)
        except requests.RequestException as exc:
            sys.exit(
                f"FEHLER beim Laden von Binance: {exc}\n"
                "Alternative: Kerzen als CSV exportieren und mit "
                "--csv datei.csv erneut starten."
            )

    if len(df) < 100:
        print(f"WARNUNG: Nur {len(df)} Kerzen geladen. EMAs brauchen eine "
              "Aufwärmphase — Werte am Tabellenanfang können von "
              "TradingView abweichen.")

    # ------------------------------------------------------------------
    # 2. Indikatoren berechnen (Parameter aus config.yaml)
    # ------------------------------------------------------------------
    df = calculate_wavetrend(
        df,
        n1=wt_cfg["n1"],
        n2=wt_cfg["n2"],
        wt2_sma_length=wt_cfg["wt2_sma_length"],
    )
    df = detect_dots(df)
    df = calculate_mfi(df, period=mfi_cfg["period"])

    # ------------------------------------------------------------------
    # 3. Tabelle der letzten N Kerzen ausgeben
    # ------------------------------------------------------------------
    table = df[["timestamp", "close", "wt1", "wt2", "dot", "mfi"]].tail(args.rows).copy()
    table["timestamp"] = table["timestamp"].dt.strftime("%Y-%m-%d %H:%M")
    table["close"] = table["close"].map("{:.1f}".format)
    table["wt1"] = table["wt1"].map("{:.2f}".format)
    table["wt2"] = table["wt2"].map("{:.2f}".format)
    table["mfi"] = table["mfi"].map("{:.2f}".format)

    print()
    print(f"Letzte {len(table)} Kerzen (Zeiten in UTC!):")
    print(table.to_string(index=False))

    # Letzte Dot-Kreuzungen hervorheben — die vergleicht man am
    # schnellsten mit den Punkten auf dem MarketCypher-Chart.
    dots = df[df["dot"] != "-"].tail(10)
    print()
    print("Letzte 10 Dot-Kreuzungen:")
    if dots.empty:
        print("  (keine im geladenen Zeitraum)")
    else:
        for _, row in dots.iterrows():
            ts = row["timestamp"].strftime("%Y-%m-%d %H:%M")
            print(f"  {ts} UTC | {row['dot']:>4} | close={row['close']:.1f} "
                  f"| wt1={row['wt1']:.2f} | wt2={row['wt2']:.2f} "
                  f"| mfi={row['mfi']:.2f}")

    print()
    print("Abgleich-Hinweis: In TradingView Symbol BINANCE:BTCUSDT, 5m. "
          "wt1/wt2 entsprechen den Market-Cipher-B-Wellen "
          "('Lt Blue Wave' = wt1, 'Blue Wave' = wt2; verifiziert 2026-06-12). "
          "Achtung: MCB 'Mny Flow' ist NICHT der Standard-MFI(14) — MFI nur "
          "gegen TradingView-Indikator 'Money Flow Index' (14) vergleichen. "
          "TradingView zeigt Zeiten in DEINER Zeitzone — diese Tabelle in UTC.")


if __name__ == "__main__":
    main()
