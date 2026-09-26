import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from urllib.parse import urlparse

import requests

from .core import Database, Engine, OVERPASS_FALLBACK, daily_due, draft_for
from .sales import acquisition_pack, marketing_draft

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOG = logging.getLogger(__name__)


def configuration():
    return {
        "token": os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
        "allowed": {int(value.strip()) for value in os.getenv("TELEGRAM_ALLOWED_USER_IDS", "").split(",") if value.strip()},
        "db": os.getenv("DATABASE_PATH", "/data/orivan.sqlite3"),
        "timezone": os.getenv("TIMEZONE", "Europe/Berlin"),
        "hour": int(os.getenv("DAILY_HOUR", "8")),
        "radius": int(os.getenv("SEARCH_RADIUS_METERS", "20000")),
        "max_audits": int(os.getenv("MAX_NEW_AUDITS", "20")),
        "daily_leads": max(1, min(10, int(os.getenv("DAILY_LEAD_LIMIT", "3")))),
        "followup_days": max(1, min(30, int(os.getenv("FOLLOWUP_DAYS", "3")))),
        "overpass_url": os.getenv("OVERPASS_URL", "https://overpass-api.de/api/interpreter"),
        "overpass_fallback_url": os.getenv("OVERPASS_FALLBACK_URL", OVERPASS_FALLBACK).strip(),
        "pagespeed_key": os.getenv("PAGESPEED_API_KEY", "").strip(),
    }


class Bot:
    def __init__(self, config):
        self.config = config
        self.db = Database(config["db"])
        self.engine = Engine(self.db, config)
        self.base = f'https://api.telegram.org/bot{config["token"]}/'
        self.offset = None
        self.worker = None

    def api(self, method, payload):
        reply = requests.post(self.base + method, json=payload, timeout=35)
        reply.raise_for_status()
        data = reply.json()
        if not data.get("ok"):
            raise RuntimeError(data.get("description", "Telegram API Fehler"))
        return data["result"]

    def send(self, chat_id, message, buttons=None):
        payload = {"chat_id": chat_id, "text": message[:4000], "disable_web_page_preview": True}
        if buttons:
            payload["reply_markup"] = {"inline_keyboard": buttons}
        return self.api("sendMessage", payload)

    def broadcast(self, message):
        for user_id in self.config["allowed"]:
            try:
                self.send(user_id, message)
            except (requests.RequestException, RuntimeError) as exc:
                LOG.warning("Benachrichtigung fehlgeschlagen (%s)", type(exc).__name__)

    def lead_text(self, lead):
        result = json.loads(lead["audit_json"] or "{}")
        parts = [f'#{lead["id"]} {lead["name"]} · {lead["profile"]}',
                 f'Priorität: {lead["score"]}/100 · Status: {lead["status"]}',
                 f'Website: {lead["website"] or "ungeklärt"}',
                 f'Quelle: {lead["source_url"]}']
        if result.get("error"):
            parts.append("Prüfung: " + result["error"])
        elif result:
            labels = {"https": "HTTPS", "viewport": "Mobiler Viewport", "h1": "H1",
                      "contact_link": "Kontaktlink", "cta_link": "Termin-/Anfragelink"}
            parts += [f'{label}: {"erkannt" if result.get(key) else "nicht erkannt"}' for key, label in labels.items()]
            if "mobile_performance" in result:
                parts.append(f'Mobile Performance: {result["mobile_performance"]}/100')
            parts.append(f'Geprüft: {result.get("checked_at", "?")}')
        if lead["draft"]:
            parts.append("\nEntwurf:\n" + lead["draft"])
        workflow = self.db.workflow(lead["id"])
        if workflow and workflow["follow_up_on"]:
            parts.append("Wiedervorlage: " + workflow["follow_up_on"])
        if workflow and workflow["note"]:
            parts.append("Notiz: " + workflow["note"])
        return "\n".join(parts)

    def buttons(self, lead_id):
        return [[{"text": "💡 Akquise-Paket", "callback_data": f'paket:{lead_id}'},
                 {"text": "⭐ Interessant", "callback_data": f'interessant:{lead_id}'}],
                [{"text": "📞 Kontaktiert", "callback_data": f'kontaktiert:{lead_id}'},
                 {"text": "📩 Antwort", "callback_data": f'antwort:{lead_id}'}],
                [{"text": "📝 Angebot", "callback_data": f'angebot:{lead_id}'},
                 {"text": "✅ Auftrag", "callback_data": f'auftrag:{lead_id}'}],
                [{"text": "📅 In 7 Tagen", "callback_data": f'later7:{lead_id}'},
                 {"text": "🗄 Archiv", "callback_data": f'archiv:{lead_id}'}]]

    def today(self):
        return datetime.now(ZoneInfo(self.config["timezone"])).date()

    def daily_brief(self, chat_id, include_marketing=False):
        day = self.today().isoformat()
        due = self.db.due_followups(day, 5)
        leads = self.db.daily_candidates(day, self.config.get("daily_leads", 3))
        self.send(chat_id, f'Orivan-Tagesliste · {day}\n'
                  f'{len(due)} fällige Wiedervorlagen (max. 5), {len(leads)} Firmen zur Prüfung.\n'
                  'Website-Befunde zuerst ansehen, dann Bedarf und passenden Kontaktweg klären. '
                  'Firmenfunde und Priorität belegen noch kein Kaufinteresse.')
        for lead in due:
            self.send(chat_id, '📅 Nächsten Schritt prüfen:\n' + self.lead_text(lead),
                      self.buttons(lead["id"]))
        for lead in leads:
            result = json.loads(lead["audit_json"] or "{}")
            self.send(chat_id, f'#{lead["id"]} · Priorität {lead["score"]}/100\n' + draft_for(lead, result),
                      self.buttons(lead["id"]))
        if not leads and not due:
            self.send(chat_id, 'Heute keine passenden geprüften Firmen oder Wiedervorlagen. '
                      '/suche findet Firmen und prüft weitere Websites; /ungeklaert zeigt offene Recherchen.')
        if include_marketing:
            self.send(chat_id, marketing_draft(day))

    def mark_status(self, chat_id, lead_id, status):
        follow_up = None
        if status in ('kontaktiert', 'angebot'):
            follow_up = (self.today() + timedelta(days=self.config.get("followup_days", 3))).isoformat()
        elif status == 'antwort':
            follow_up = self.today().isoformat()
        if self.db.status(lead_id, status, follow_up):
            workflow = self.db.workflow(lead_id)
            extra = f' · Wiedervorlage: {workflow["follow_up_on"]}' if workflow and workflow["follow_up_on"] else ''
            self.send(chat_id, f'#{lead_id}: Status auf {status} gesetzt{extra}.')

    def launch(self, profile, chat_id, scheduled=False):
        if self.worker and self.worker.is_alive():
            if not scheduled:
                self.send(chat_id, "Es läuft bereits eine Suche.")
            return False
        self.send(chat_id, "Suche gestartet. Das Ergebnis kommt nach Abschluss als Nachricht.")

        def job():
            try:
                summary = self.engine.run(profile)
                self.send(chat_id, summary)
            except Exception:
                LOG.exception("Suche fehlgeschlagen")
                self.send(chat_id, "Suche fehlgeschlagen. Details stehen im Container-Log.")
            finally:
                try:
                    self.daily_brief(chat_id, include_marketing=scheduled)
                except Exception:
                    LOG.exception("Tagesliste konnte nicht gesendet werden")

        self.worker = threading.Thread(target=job, daemon=True)
        self.worker.start()
        return True

    def command(self, user_id, chat_id, message):
        parts = message.strip().split(maxsplit=1)
        if not parts:
            return
        command = parts[0].split("@")[0].lower()
        argument = parts[1] if len(parts) > 1 else ""
        args = argument.split()
        if command == "/id":
            self.send(chat_id, f"Deine Telegram-Nutzer-ID: {user_id}")
            return
        if user_id not in self.config["allowed"] or chat_id != user_id:
            return
        if command in ("/start", "/hilfe"):
            self.send(chat_id, "Orivan Engine\n/suche [handwerk|praxis|dienstleister|alle]\n"
                      "/leads · /lead ID · /ungeklaert · /status · /plan [starten|pausieren]\n"
                      "/heute · /paket ID · /marketing · /pipeline\n"
                      "/notiz ID Text · /wiedervorlage ID Tage (oder aus)\n"
                      "/firma Name | Website oder - | Herkunft\n"
                      "© OpenStreetMap contributors: https://www.openstreetmap.org/copyright")
        elif command == "/heute":
            self.daily_brief(chat_id)
        elif command == "/marketing":
            self.send(chat_id, marketing_draft(self.today().isoformat()))
        elif command == "/firma":
            fields = [value.strip() for value in argument.split('|')]
            if len(fields) != 3 or not all(fields) or len(fields[0]) > 200 or len(fields[2]) > 200:
                self.send(chat_id, 'Nutzung: /firma Name | Website oder - | Herkunft (z. B. Empfehlung).')
                return
            name, website, source = fields
            website = None if website == '-' else website
            if website:
                if '://' not in website:
                    website = 'https://' + website
                try:
                    parsed = urlparse(website)
                    valid = (parsed.scheme in ('http', 'https') and parsed.hostname
                             and not parsed.username and not parsed.password and len(website) <= 500)
                except ValueError:
                    valid = False
                if not valid:
                    self.send(chat_id, 'Bitte eine HTTP(S)-Website oder - eintragen.')
                    return
            lead_id = self.db.add_manual(name, website, source)
            if not self.db.workflow(lead_id):
                self.db.set_followup(lead_id, self.today().isoformat())
            self.send(chat_id, f'Firma gespeichert: #{lead_id}. Mit /lead {lead_id} öffnen. '
                      'Website-Prüfung folgt beim nächsten Suchlauf.', self.buttons(lead_id))
        elif command == "/pipeline":
            counts = self.db.pipeline()
            order = ('neu', 'interessant', 'kontaktiert', 'antwort', 'angebot', 'auftrag', 'archiv')
            self.send(chat_id, 'Aktueller Vertriebsstand\n' + '\n'.join(f'{key}: {counts.get(key, 0)}' for key in order)
                      + '\nDie Zahlen zeigen den aktuellen Status, keine historischen Abschlussquoten.')
        elif command == "/paket":
            lead = self.db.get(int(args[0])) if args and args[0].isdigit() else None
            self.send(chat_id, acquisition_pack(lead) if lead else 'Nutzung: /paket ID – ID muss vorhanden sein.')
        elif command == "/notiz":
            note_args = argument.split(maxsplit=1)
            if len(note_args) == 2 and note_args[0].isdigit() and len(note_args[1]) <= 800:
                saved = self.db.set_note(int(note_args[0]), note_args[1])
                self.send(chat_id, 'Notiz gespeichert.' if saved else 'Lead nicht gefunden.')
            else:
                self.send(chat_id, 'Nutzung: /notiz ID Text (max. 800 Zeichen; ersetzt die bisherige Notiz).')
        elif command == "/wiedervorlage":
            valid = (len(args) == 2 and args[0].isdigit() and
                     (args[1].lower() == 'aus' or (args[1].isdigit() and 0 <= int(args[1]) <= 365)))
            if valid:
                day = None if args[1].lower() == 'aus' else (self.today() + timedelta(days=int(args[1]))).isoformat()
                saved = self.db.set_followup(int(args[0]), day)
                self.send(chat_id, f'Wiedervorlage: {day or "aus"}.' if saved else 'Lead fehlt oder ist abgeschlossen/archiviert.')
            else:
                self.send(chat_id, 'Nutzung: /wiedervorlage ID Tage (0–365) oder /wiedervorlage ID aus.')
        elif command == "/suche":
            profile = args[0].lower() if args else "alle"
            if profile not in ("alle", "handwerk", "praxis", "dienstleister"):
                self.send(chat_id, "Gültig: /suche handwerk, praxis, dienstleister oder alle. Gebiet: Passau + Suchradius.")
            else:
                self.launch(profile, chat_id)
        elif command == "/leads":
            leads = self.db.list(5)
            if not leads:
                self.send(chat_id, "Noch keine geprüften Leads. Starte /suche.")
            for lead in leads:
                self.send(chat_id, self.lead_text(lead), self.buttons(lead["id"]))
        elif command == "/lead" and args and args[0].isdigit():
            lead = self.db.get(int(args[0]))
            self.send(chat_id, self.lead_text(lead), self.buttons(lead["id"])) if lead else self.send(chat_id, "Lead nicht gefunden.")
        elif command == "/ungeklaert":
            with self.db.lock:
                leads = self.db.conn.execute("SELECT * FROM leads WHERE website IS NULL AND status='neu' LIMIT 10").fetchall()
            self.send(chat_id, "\n".join(f'#{x["id"]} {x["name"]} · {x["source_url"]}' for x in leads)
                      if leads else "Keine ungeklärten Websites.")
        elif command == "/status":
            running = bool(self.worker and self.worker.is_alive())
            self.send(chat_id, f'Unternehmen: {self.db.count()} · Suche aktiv: {running}\n'
                      f'Letzter Lauf: {self.db.setting("last_run", "noch keiner")}')
        elif command == "/plan":
            if args and args[0].lower() in ("pausieren", "starten"):
                self.db.set("schedule", "off" if args[0].lower() == "pausieren" else "on")
            self.send(chat_id, f'Tägliche Suche: {self.db.setting("schedule", "on")} · '
                      f'{self.config["hour"]:02d}:00 {self.config["timezone"]} · '
                      f'Passau + {self.config["radius"] // 1000} km · '
                      f'max. {self.config["max_audits"]} Prüfungen')
        else:
            self.send(chat_id, "Unbekannter Befehl. /hilfe zeigt alle Befehle.")

    def callback(self, callback):
        user_id = callback["from"]["id"]
        chat_id = callback.get("message", {}).get("chat", {}).get("id")
        self.api("answerCallbackQuery", {"callback_query_id": callback["id"]})
        if user_id not in self.config["allowed"] or chat_id != user_id:
            return
        try:
            action, lead_id_text = callback.get("data", "").split(":", 1)
            lead_id = int(lead_id_text)
        except (ValueError, TypeError):
            return
        lead = self.db.get(lead_id)
        if not lead:
            self.send(chat_id, "Lead nicht gefunden.")
            return
        if action == "paket":
            self.send(chat_id, acquisition_pack(lead))
        elif action == "later7":
            day = (self.today() + timedelta(days=7)).isoformat()
            saved = self.db.set_followup(lead_id, day)
            self.send(chat_id, f'#{lead_id}: Wiedervorlage am {day}.' if saved else 'Lead ist bereits abgeschlossen/archiviert.')
        elif action == "draft":
            result = json.loads(lead["audit_json"] or "{}")
            if result.get("error") or not result:
                self.send(chat_id, "Kein belegter Website-Befund für einen Entwurf vorhanden.")
                return
            content = draft_for(lead, result)
            self.db.draft(lead_id, content)
            self.send(chat_id, f'#{lead_id} – Entwurf:\n{content}')
        else:
            self.mark_status(chat_id, lead_id, action)

    def scheduled_tick(self):
        if daily_due(self.db, self.config):
            day = self.today().isoformat()
            if self.config["allowed"] and self.launch("alle", min(self.config["allowed"]), scheduled=True):
                self.db.set("daily_date", day)

    def poll(self):
        while True:
            try:
                self.scheduled_tick()
                result = self.api("getUpdates", {"offset": self.offset, "timeout": 20,
                                                  "allowed_updates": ["message", "callback_query"]})
                for update in result:
                    self.offset = update["update_id"] + 1
                    if "callback_query" in update:
                        self.callback(update["callback_query"])
                    elif "message" in update and update["message"].get("text", "").startswith("/"):
                        msg = update["message"]
                        self.command(msg["from"]["id"], msg["chat"]["id"], msg["text"])
            except (requests.RequestException, RuntimeError, KeyError, ValueError) as exc:
                LOG.warning("Bot-Fehler (%s); neuer Versuch", type(exc).__name__)
                time.sleep(5)


if __name__ == "__main__":
    config = configuration()
    if not config["token"]:
        raise SystemExit("TELEGRAM_BOT_TOKEN fehlt. Siehe .env.example")
    if not config["allowed"]:
        LOG.warning("Keine Nutzer-ID freigegeben. Nur /id ist verfügbar.")
    bot = Bot(config)
    if os.getenv("ORIVAN_METRICS_ENABLED") == "1":
        from .metrics import start_metrics
        start_metrics(bot.db, config["timezone"], bot.engine)
    bot.poll()
