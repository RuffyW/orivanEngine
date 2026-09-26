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

## Täglicher Akquise-Ablauf – kostenlos auf dem Pi

Nach dem täglichen Suchlauf kommt eine Telegram-Tagesliste mit fälligen
Wiedervorlagen, bis zu drei geprüften Firmen und einer Marketingvorlage.
Firmen mit laufenden Gesprächen werden über Wiedervorlagen betreut. Die
Tagesauswahl berücksichtigt Prüfungen der letzten 30 Tage mit Priorität ab 40;
am Folgetag kommen zunächst noch nicht vorgestellte Firmen zum Zug. Wiederholtes
`/heute` hält die Auswahl stabil, solange sich deren Status nicht ändert.
Die automatische Nachricht geht wie bisher an die kleinste freigegebene Nutzer-ID;
alle freigegebenen Nutzer können `/heute` aufrufen und arbeiten am selben Bestand.

| Befehl / Button | Zweck |
| --- | --- |
| `/heute` | Tagesliste und bis zu fünf fällige Wiedervorlagen |
| `/paket 20` oder Akquise-Paket | Belegter Website-Hinweis, Einstiegsangebot, Gesprächsfragen und Text für eine angefragte Analyse |
| `/notiz 20 Rückruf mit Frau Müller vereinbart` | Gesprächsnotiz speichern (max. 800 Zeichen; ersetzt die vorherige Notiz) |
| `/wiedervorlage 20 7` | Interne Erinnerung in sieben Tagen |
| `/wiedervorlage 20 aus` | Erinnerung entfernen |
| Kontaktiert / Angebot | Status setzen und standardmäßig in drei Tagen intern erinnern |
| Antwort | Gespräch heute zur Bearbeitung vorlegen |
| Auftrag / Archiv | Gespräch abschließen und Erinnerung entfernen |
| `/firma Muster GmbH \| https://muster.example \| Empfehlung` | Eingehende Anfrage oder empfohlenen Betrieb aufnehmen; für unbekannte Website `-` verwenden |
| `/marketing` | Kurze Textvorlage mit Einladung zur kostenlosen Startseitenanalyse |
| `/pipeline` | Aktuelle Anzahlen je Vertriebsstatus |

`DAILY_LEAD_LIMIT=3` und `FOLLOWUP_DAYS=3` sind die Standardwerte. Bestehende
`.env`-Dateien brauchen dafür keine neuen Einträge. Eine neue Tabelle ergänzt
die vorhandene Datenbank; bestehende Firmen und Gesprächsnotizen bleiben erhalten.
Bereits laufende Gespräche ohne bisherigen Workflow erscheinen nach dem Update
zunächst als fällige Wiedervorlage. Auch bei einem fehlgeschlagenen Suchlauf wird
versucht, die Tagesliste aus dem vorhandenen Bestand zu liefern.

### So nutzt ihr die Vorbereitung

1. Die vorgeschlagenen Startseiten kurz selbst prüfen. Ein nicht erkannter Link
   beweist weder einen verlorenen Kunden noch Kaufinteresse.
2. Mit einem kleinen Angebot beginnen: eine kostenlose Analyse einer Startseite
   mit drei konkreten Hinweisen. Im Gespräch Ziel, Entscheidungsweg und Bedarf
   klären; erst danach einen abgegrenzten bezahlten Schritt anbieten.
3. Nach jedem Gespräch Status, Ergebnis und vereinbarten nächsten Termin im Bot
   festhalten. Die Wiedervorlage erinnert euch intern und versendet keine Werbung.
4. Zwei Marketingvorlagen pro Woche mit einem eigenen aktuellen Beispiel ergänzen
   und auf eurem Orivan-Kanal veröffentlichen. Die sieben Themen wiederholen sich
   wöchentlich; es handelt sich um Vorlagen, nicht um täglich neu recherchierte Posts.
5. Antworten auf die Einladung „CHECK“ und Empfehlungen mit `/firma` aufnehmen.
   Nach einigen tatsächlichen Gesprächen auswerten, welche Branche und welches
   Angebot Interesse erzeugen. Grafana zeigt Antworten, offene Angebote, Aufträge
   und fällige Wiedervorlagen als aktuelle Bestandszahlen, keine Abschlussprognose.

Die Vorbereitung braucht keine KI, neuen Dienste oder kostenpflichtigen APIs.
Es bleibt bei höchstens 20 nacheinander geprüften Websites pro Suchlauf
(über `MAX_NEW_AUDITS` einstellbar). Recherche, Vorlagen und Erinnerungen laufen
auf dem Pi; ihr übernehmt Veröffentlichung, Gespräche und Angebotsabstimmung.
`/plan pausieren` pausiert den automatischen Suchlauf samt Tagesnachricht;
die manuellen Befehle bleiben verfügbar.

Für Werbe-E-Mails gilt grundsätzlich das Erfordernis vorheriger ausdrücklicher
Einwilligung; es gibt eine enge Bestandskundenausnahme. B2B-Telefonwerbung verlangt
zumindest mutmaßliche Einwilligung. Ein Website-Befund allein ist keine
Kontaktfreigabe. Quelle: [§ 7 UWG](https://www.gesetze-im-internet.de/uwg_2004/__7.html).

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
Suchlauf. Eine Tabelle zeigt bis zu 100 nicht archivierte Firmen nach Priorität
mit Name, Branche, Status und Website. Die Namen und Websites dieser Firmen
werden für die Tabelle an den lokalen Prometheus-Dienst übertragen. Archivierte
Firmen und Entwürfe werden nicht exportiert. Die Zeitreihen beginnen beim ersten
Start des Monitorings und werden bis zu sieben Tage aufbewahrt (maximal 256 MB
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
`/plan`, `/plan pausieren`, `/plan starten`, `/heute`, `/paket 42`,
`/marketing`, `/pipeline`, `/firma`, `/notiz`, `/wiedervorlage`.

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
- Gibt die primäre Overpass-Instanz einen Gateway-Fehler (502/503/504) zurück
  oder bricht die Verbindung ab, versucht die Engine die jeweilige Branche
  einmal bei `overpass.private.coffee`. Bei 429/406 wird nicht sofort erneut
  angefragt. Den zweiten Endpunkt kannst du mit `OVERPASS_FALLBACK_URL` in
  `.env` ändern oder mit einem leeren Wert deaktivieren. Für verlässliche
  gewerbliche Nutzung empfiehlt sich ein eigener oder bezahlter Dienst.
  Umfang und Takt der Suchanfragen bleiben bewusst niedrig.
- Datenquelle: © OpenStreetMap contributors,
  https://www.openstreetmap.org/copyright (ODbL).

## Lokale Prüfung

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v
```
