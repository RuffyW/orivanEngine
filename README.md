# Orivan Engine v1

Lokale Lead-Suche für einen Raspberry Pi 5. Findet Betriebe im Umkreis von Passau über
OpenStreetMap/Overpass, prüft verlinkte Websites und meldet priorisierte Funde per Telegram.
Der Bot erstellt auf Knopfdruck eine kurze Gesprächsnotiz aus den belegten
Prüfergebnissen. Es werden keine Akquise-Nachrichten automatisch verschickt.

## Voraussetzungen

- Raspberry Pi OS 64-bit, Docker Engine und Docker Compose Plugin.
- Telegram-Bot von [@BotFather](https://t.me/BotFather) und dessen Token.
- Deine numerische Telegram-Nutzer-ID; der Bot zeigt sie mit `/id` an.
- Optional: ein PageSpeed-API-Key.

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

## Update von der ersten Version

Der Entwurf läuft ohne lokales Sprachmodell. Das vermeidet lange Wartezeiten und
hohe Last auf dem Pi. Nach `git pull` den Dienst neu bauen und den alten
Ollama-Container dieses Compose-Projekts entfernen:

```bash
docker compose up -d --build --remove-orphans
```

Falls nur das eigenständige `docker-compose` auf deinem Pi funktioniert,
verwende `docker-compose up -d --build --remove-orphans`. Der zuvor geladene
Modell-Volume wird dabei nicht gelöscht. Alte `OLLAMA_*`-Einträge in der
eigenen `.env` haben nach dem Update keine Wirkung mehr.

## Grafana (optional)

Das Dashboard zeigt die Gesamtzahl, offene Leads mit Priorität ab 70,
heutige Funde und Prüfungen, Status, Branchen, den Verlauf sowie den letzten
Suchlauf. Es nutzt nur aggregierte Zähler; Firmennamen und Websites werden
nicht an Prometheus übertragen. Die Zeitreihen beginnen beim ersten Start
des Monitorings und werden bis zu sieben Tage aufbewahrt (maximal 256 MB
Prometheus-Datenblöcke). Die Dashboard-Daten bleiben in einem Docker-Volume.

In `.env` ein eigenes, langes `GRAFANA_ADMIN_PASSWORD` eintragen. Dann:

```bash
git pull --ff-only
docker-compose -f compose.yaml -f compose.grafana.yaml up -d --build --remove-orphans
```

Falls das Compose-Plugin auf deinem Pi funktioniert, kannst du stattdessen
`docker compose -f compose.yaml -f compose.grafana.yaml up -d --build --remove-orphans`
verwenden. Grafana lauscht nur auf `127.0.0.1:3000` des Pi. Vom eigenen
Rechner aus einen SSH-Tunnel öffnen und `http://localhost:3000` aufrufen:

```bash
ssh -L 3000:127.0.0.1:3000 PI_BENUTZER@PI_ADRESSE
```

Anmeldung: `admin` und das Passwort aus `.env`. Das Dashboard liegt im Ordner
**Orivan**. Beim ersten Start bis zu einer Minute auf die erste Messung warten.
Prometheus und Grafana sind zusätzliche Prozesse auf dem Pi; bei knappen
Ressourcen kannst du sie mit `docker-compose -f compose.yaml -f compose.grafana.yaml stop grafana prometheus`
anhalten. Die normale Engine läuft weiter. Zum erneuten Start den obigen
`up`-Befehl nutzen. Die Volumes mit `down -v` nicht löschen, wenn die
gespeicherten Diagrammdaten erhalten bleiben sollen.

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
- Der Entwurf verwendet keine Website-Titel oder Meta-Beschreibungen als Text.
  Nicht erkannte Links sind Hinweise für die manuelle Prüfung, keine gesicherten
  Aussagen über das gesamte Angebot des Unternehmens.
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
