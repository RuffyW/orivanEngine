import json
import logging
import os
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

from .core import Database, Engine, daily_due, draft_for

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
        "overpass_url": os.getenv("OVERPASS_URL", "https://overpass-api.de/api/interpreter"),
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
        return "\n".join(parts)

    def buttons(self, lead_id):
        return [[{"text": "💡 Entwurf", "callback_data": f'draft:{lead_id}'},
                 {"text": "⭐ Interessant", "callback_data": f'interessant:{lead_id}'}],
                [{"text": "📞 Kontaktiert", "callback_data": f'kontaktiert:{lead_id}'},
                 {"text": "📩 Antwort", "callback_data": f'antwort:{lead_id}'}],
                [{"text": "✅ Auftrag", "callback_data": f'auftrag:{lead_id}'},
                 {"text": "🗄 Archiv", "callback_data": f'archiv:{lead_id}'}]]

    def launch(self, profile, chat_id):
        if self.worker and self.worker.is_alive():
            self.send(chat_id, "Es läuft bereits eine Suche.")
            return
        self.send(chat_id, "Suche gestartet. Das Ergebnis kommt nach Abschluss als Nachricht.")

        def job():
            try:
                summary = self.engine.run(profile)
                self.send(chat_id, summary)
                top = self.db.list(5)
                if top:
                    self.send(chat_id, "Beste geprüfte Chancen:")
                    for lead in top:
                        self.send(chat_id, self.lead_text(lead), self.buttons(lead["id"]))
            except Exception:
                LOG.exception("Suche fehlgeschlagen")
                self.send(chat_id, "Suche fehlgeschlagen. Details stehen im Container-Log.")

        self.worker = threading.Thread(target=job, daemon=True)
        self.worker.start()

    def command(self, user_id, chat_id, message):
        command, *args = message.strip().lower().split()
        command = command.split("@")[0]
        if command == "/id":
            self.send(chat_id, f"Deine Telegram-Nutzer-ID: {user_id}")
            return
        if user_id not in self.config["allowed"] or chat_id != user_id:
            return
        if command in ("/start", "/hilfe"):
            self.send(chat_id, "Orivan Engine\n/suche [handwerk|praxis|dienstleister|alle]\n"
                      "/leads · /lead ID · /ungeklaert · /status · /plan [starten|pausieren]\n"
                      "© OpenStreetMap contributors: https://www.openstreetmap.org/copyright")
        elif command == "/suche":
            profile = args[0] if args else "alle"
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
            if args and args[0] in ("pausieren", "starten"):
                self.db.set("schedule", "off" if args[0] == "pausieren" else "on")
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
        if action == "draft":
            result = json.loads(lead["audit_json"] or "{}")
            if result.get("error") or not result:
                self.send(chat_id, "Kein belegter Website-Befund für einen Entwurf vorhanden.")
                return
            content = draft_for(lead, result)
            self.db.draft(lead_id, content)
            self.send(chat_id, f'#{lead_id} – Entwurf:\n{content}')
        elif self.db.status(lead_id, action):
            self.send(chat_id, f'#{lead_id}: Status auf {action} gesetzt.')

    def poll(self):
        while True:
            try:
                if daily_due(self.db, self.config):
                    day = datetime.now(ZoneInfo(self.config["timezone"])).date().isoformat()
                    self.db.set("daily_date", day)
                    if self.config["allowed"]:
                        self.launch("alle", min(self.config["allowed"]))
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
