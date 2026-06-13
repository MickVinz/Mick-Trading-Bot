# Projekt: Mick Trading Bot (5m Anchor-Trigger Scalp)

## Modell-Hinweis (vom User gewünscht 2026-06-12 — Token sparen)

**Bei JEDER Antwort** in der ERSTEN Zeile einen kurzen Modell-Hinweis ausgeben,
welches Modell für die aktuelle Aufgabe ausreicht. Format:

> 💡 **Modell:** <Empfehlung> — <Ein-Satz-Grund>

Entscheidungslogik (Aufgabe → ausreichendes Modell):

| Aufgabentyp | Ausreichend |
|---|---|
| Routine-Code, Tests, Skripte, Doku, nach Spec/Muster bauen | **Sonnet 4.6** |
| Design/Architektur, heikle Finanz-Logik (Sizing, PnL, Exit-Konflikte), unklares Debugging | **Opus 4.8** |
| Tippfehler, Rename, triviale 1-Zeilen-Edits | **Haiku 4.5** |
| Reine Frage/Beratung ohne Code | **Sonnet 4.6** (oft Haiku ok) |

Regeln:
- Standard ist **Sonnet 4.6**. Nur bei „hier darf nichts schiefgehen" oder
  „ich versteh den Bug nicht" auf **Opus 4.8** hochschalten.
- 1M-Kontext NICHT nötig — Codebase ist klein.
- Wenn aktuelles Modell höher ist als nötig: das ehrlich sagen
  (z.B. „Opus 4.8 läuft, Sonnet 4.6 würde reichen").
- Hinweis kurz halten (eine Zeile), nicht den ganzen Token-Spareffekt auffressen.

## Projekt-Kontext

- BTC/USDT Perpetual Futures (BingX), 5m, indikatorbasiert.
- Phase: **Paper Trading** — KEINE echten Orders, keine Live-Order-Calls.
- API-Keys NUR aus `.env` (gitignored), niemals in Code/Commit/Chat.
- Spec: `Jason Casper Markdown/strategy_spec_5m_anchor_trigger.md`
- Parameter in `config/config.yaml` (nicht hart kodieren).
- Antworten auf Deutsch.

## Session-Workflow (Speicherstand-System)

`session-status.md` ist der Speicherstand dieses Projekts. Jeder Chat startet bei null und liest sich darüber ein.

### START (neues Terminal)
1. `session-status.md` lesen.
2. Aktuellen Stand + offene Schritte kurz nennen.
3. Auf Aufgabe des Users warten.

### ENDE (User sagt "fertig"/"Feierabend"/"abschließen")
1. Commit vorschlagen (deutsche Message, sicherheitskritische Dateien wie `.env` NICHT includen). **Erst nach OK committen.**
2. Push vorschlagen. **Erst nach OK pushen.**
3. Compare-Link liefern: `https://github.com/MickVinz/Mick-Trading-Bot/compare/main...<branch>`
   plus fertigen PR-Text (Titel + kurze Beschreibung) — User mergt per Klick (gh CLI ist NICHT installiert).
4. `session-status.md` pflegen: Erledigtes ins Archiv, neue offene Schritte eintragen.
5. Dem User sagen, dass er `/clear` machen kann.
