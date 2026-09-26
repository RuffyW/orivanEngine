# Orivan Engine v1

Lokale Lead-Suche für einen Raspberry Pi 5. Findet Betriebe im Umkreis von Passau über
OpenStreetMap/Overpass, prüft verlinkte Websites und meldet priorisierte Funde per Telegram.
Die KI formuliert auf Knopfdruck einen beleggebundenen Entwurf. Es werden keine
Akquise-Nachrichten automatisch verschickt.

## Voraussetzungen

- Raspberry Pi OS 64-bit, Docker Engine und Docker Compose Plugin.
- Telegram-Bot von [@BotFather](https://t.me/BotFather) und dessen Token.
- Deine numerische Telegram-Nutzer-ID; der Bot zeigt sie mit `/id` an.
- Optional: ein kleines Ollama-Textmodell und ein PageSpeed-API-Key.

## Installation

```bash
cp .env.example .env
mkdir -p data
```

In `.env` zunächst `TELEGRAM_BOT_TOKEN` eintragen. Danach starten:

```bash
docker compose up -d --build
```

Dem Bot privat `/id` schicken, `TELEGRAM_ALLOWED_USER_IDS` in `.env` mit der
angezeigten Zahl befüllen und `docker compose up -d --force-recreate engine`
ausführen. Die `.env`-Datei niemals veröffentlichen. Mehrere berechtigte IDs
können durch Kommas getrennt werden.

Danach im privaten Bot-Chat `/suche alle` starten. Mit `/plan pausieren` lässt
sich die automatische tägliche Suche anhalten. Standardmäßig läuft sie um
08:00 Uhr Europe/Berlin und prüft höchstens 20 neue Websites pro Durchlauf.
Der Suchradius wird in `.env` eingestellt; Version 1 ist auf Passau zentriert.

## KI einschalten

Ohne Modell läuft die Lead-Suche weiter; die Schaltfläche „Entwurf“ meldet,
dass noch kein Modell konfiguriert ist. Für einen ersten Versuch ein kleines,
quantisiertes, deutschsprachig brauchbares Ollama-Modell auswählen und auf dem
Pi laden, zum Beispiel mit:

```bash
docker compose exec ollama ollama pull qwen2.5:1.5b
```

Dann `OLLAMA_MODEL=qwen2.5:1.5b` in `.env` setzen und den Engine-Container
neu erstellen. Die Antwortgeschwindigkeit hängt von RAM-Variante und anderen
Pi-Diensten ab. Ollama wird nur innerhalb des Compose-Netzes angeboten.

## Befehle

`/hilfe`, `/id`, `/suche alle`, `/suche handwerk`, `/suche praxis`,
`/suche dienstleister`, `/leads`, `/lead 42`, `/ungeklaert`, `/status`,
`/plan`, `/plan pausieren`, `/plan starten`.

## Daten und Grenzen

- Fundstellen und Datum werden in `data/orivan.sqlite3` gespeichert. Für eine
  Sicherung den Container stoppen und die Datenbankdatei kopieren.
- Eine nicht hinterlegte Website wird als „ungeklärt“ geführt. Nicht erkannte
  Elemente sind keine Aussage darüber, ob sie anderswo auf der Website existieren.
- Die Prüfung liest die Startseite, beachtet `robots.txt` und begrenzt HTML auf
  1 MB. Sie ist kein vollständiger SEO-, Barrierefreiheits- oder Rechtstest.
- Ohne `PAGESPEED_API_KEY` wird keine Performance-Zahl behauptet. Mit Key wird
  die mobile PageSpeed-Analyse für geprüfte Websites angefragt; es können
  externe Kosten oder Kontingentgrenzen gelten.
- Die öffentliche Overpass-Instanz kann Anfragen begrenzen. Umfang und Takt
  sind bewusst niedrig; bei größerem Umfang braucht es eine geeignete Quelle.
- Datenquelle: © OpenStreetMap contributors,
  https://www.openstreetmap.org/copyright (ODbL).

## Lokale Prüfung

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v
```
