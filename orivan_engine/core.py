import ipaddress
import json
import logging
import os
import re
import socket
import sqlite3
import threading
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser
from zoneinfo import ZoneInfo

import requests

LOG = logging.getLogger(__name__)
USER_AGENT = "OrivanResearchBot/0.1 (low-volume website audit)"
PROFILES = {
    "handwerk": [('craft', 'plumber|electrician|carpenter|roofer|painter|hvac|heating_engineer|tiler')],
    "praxis": [('amenity', 'doctors|dentist|veterinary|clinic')],
    "dienstleister": [('office', 'lawyer|accountant|insurance|estate_agent|consulting')],
}
PASSAU = (48.574, 13.456)
STATUS = {"neu", "interessant", "kontaktiert", "antwort", "auftrag", "archiv"}


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def public_url(value):
    if not value:
        return None
    value = value.strip()
    if not re.match(r"^https?://", value, re.I):
        value = "https://" + value
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        return None
    if parsed.port and parsed.port not in (80, 443):
        return None
    host = parsed.hostname
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, None)}
    except (OSError, ValueError):
        return None
    if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
        return None
    return value


class SafeSession(requests.Session):
    def request(self, method, url, **kwargs):
        if not public_url(url):
            raise ValueError("URL ist nicht öffentlich erreichbar")
        kwargs.setdefault("timeout", 12)
        kwargs.setdefault("headers", {"User-Agent": USER_AGENT})
        # Session.get defaults to following redirects; override it explicitly.
        kwargs["allow_redirects"] = False
        response = super().request(method, url, **kwargs)
        for _ in range(4):
            if not response.is_redirect:
                return response
            target = requests.compat.urljoin(response.url, response.headers.get("Location", ""))
            response.close()
            if not public_url(target):
                raise ValueError("Weiterleitung auf nicht öffentliche URL")
            response = super().request(method, target, allow_redirects=False, timeout=12,
                                       headers={"User-Agent": USER_AGENT}, stream=kwargs.get("stream", False))
        response.close()
        raise ValueError("Zu viele Weiterleitungen")


class SiteParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.title = ""
        self.description = ""
        self.viewport = False
        self.h1 = False
        self.contact = False
        self.cta = False
        self._title = False
        self._h1 = False
        self._visible = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "title":
            self._title = True
        if tag == "h1":
            self._h1 = True
            self.h1 = True
        if tag == "meta" and attrs.get("name", "").lower() == "viewport":
            self.viewport = bool(attrs.get("content"))
        if tag == "meta" and attrs.get("name", "").lower() == "description":
            self.description = attrs.get("content", "").strip()
        if tag == "a":
            href = attrs.get("href", "").lower()
            self.contact |= href.startswith(("mailto:", "tel:")) or "kontakt" in href
            self.cta |= any(word in href for word in ("termin", "anfrage", "buchung", "appointment"))

    def handle_endtag(self, tag):
        if tag == "title":
            self._title = False
        if tag == "h1":
            self._h1 = False

    def handle_data(self, data):
        if self._title:
            self.title += data
        if len(self._visible) < 1000:
            self._visible.append(data)

    def findings(self):
        visible = " ".join(self._visible).lower()
        return {
            "title": self.title.strip()[:200], "description": self.description[:300],
            "viewport": self.viewport, "h1": self.h1, "contact_link": self.contact,
            "cta_link": self.cta, "contact_text": "kontakt" in visible,
        }


def audit(url, pagespeed_key=""):
    safe = public_url(url)
    if not safe:
        return {"error": "Keine sichere öffentliche Website-URL"}
    session = SafeSession()
    try:
        parsed = urlparse(safe)
        robots = RobotFileParser()
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        rr = session.get(robots_url, stream=True)
        if rr.ok:
            robots_data = bytearray()
            for chunk in rr.iter_content(8192):
                robots_data.extend(chunk)
                if len(robots_data) >= 200_000:
                    break
            robots.parse(robots_data.decode("utf-8", errors="replace").splitlines())
            if not robots.can_fetch(USER_AGENT, safe):
                return {"error": "robots.txt untersagt die Prüfung"}
        rr.close()
        response = session.get(safe, stream=True)
        response.raise_for_status()
        if "html" not in response.headers.get("Content-Type", "").lower():
            return {"error": "Keine HTML-Seite"}
        data = bytearray()
        for chunk in response.iter_content(16384):
            data.extend(chunk)
            if len(data) > 1_000_000:
                return {"error": "HTML größer als 1 MB"}
        final_url = response.url
        response.close()
        parser = SiteParser()
        parser.feed(data.decode(response.encoding or "utf-8", errors="replace"))
        result = parser.findings()
        result.update({"url": final_url, "https": urlparse(final_url).scheme == "https", "checked_at": now()})
        if pagespeed_key:
            try:
                psi = requests.get("https://www.googleapis.com/pagespeedonline/v5/runPagespeed",
                                   params={"url": final_url, "strategy": "mobile", "key": pagespeed_key}, timeout=40)
                psi.raise_for_status()
                score = psi.json()["lighthouseResult"]["categories"]["performance"]["score"]
                result["mobile_performance"] = round(score * 100)
            except (requests.RequestException, KeyError, TypeError, ValueError) as exc:
                result["pagespeed_error"] = str(exc)[:160]
        return result
    except (requests.RequestException, ValueError, UnicodeError) as exc:
        return {"error": str(exc)[:200]}
    finally:
        session.close()


def score(profile, result):
    if result.get("error"):
        return 0
    # Sales priority, not a general website quality score.
    points = 30 if profile in PROFILES else 0
    points += 10 if not result.get("https") else 0
    points += 8 if not result.get("viewport") else 0
    points += 5 if not result.get("title") else 0
    points += 5 if not result.get("description") else 0
    points += 5 if not result.get("h1") else 0
    points += 10 if not result.get("contact_link") else 0
    points += 10 if not result.get("cta_link") else 0
    speed = result.get("mobile_performance")
    points += 10 if speed is not None and speed < 50 else 0
    return min(points, 100)


class Database:
    def __init__(self, path):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock, self.conn:
            self.conn.executescript("""
                CREATE TABLE IF NOT EXISTS leads (
                    id INTEGER PRIMARY KEY, source_id TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
                    profile TEXT NOT NULL, website TEXT, source_url TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'neu', score INTEGER NOT NULL DEFAULT 0,
                    audit_json TEXT, draft TEXT, discovered_at TEXT NOT NULL, checked_at TEXT
                );
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """)
            version = self.conn.execute("SELECT value FROM settings WHERE key='draft_version'").fetchone()
            if not version or version[0] != "2":
                self.conn.execute("UPDATE leads SET draft=NULL")
                self.conn.execute("""INSERT INTO settings(key,value) VALUES('draft_version','2')
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value""")

    def setting(self, key, default=None):
        with self.lock:
            row = self.conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return row[0] if row else default

    def set(self, key, value):
        with self.lock, self.conn:
            self.conn.execute("INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))

    def add(self, item):
        with self.lock, self.conn:
            cursor = self.conn.execute("""INSERT OR IGNORE INTO leads
                (source_id,name,profile,website,source_url,discovered_at)
                VALUES (:source_id,:name,:profile,:website,:source_url,:discovered_at)""", item)
            return cursor.rowcount

    def pending(self, limit):
        with self.lock:
            return self.conn.execute("""SELECT * FROM leads WHERE website IS NOT NULL
                AND status NOT IN ('archiv','auftrag') AND
                (checked_at IS NULL OR (checked_at < datetime('now', '-30 days')
                AND json_extract(audit_json, '$.error') IS NULL) OR
                (checked_at < datetime('now', '-7 days')
                AND json_extract(audit_json, '$.error') IS NOT NULL))
                ORDER BY checked_at IS NOT NULL, checked_at ASC LIMIT ?""", (limit,)).fetchall()

    def update_audit(self, lead_id, result, priority):
        with self.lock, self.conn:
            self.conn.execute("UPDATE leads SET audit_json=?, score=?, checked_at=? WHERE id=?",
                              (json.dumps(result, ensure_ascii=False), priority, now(), lead_id))

    def list(self, limit=5):
        with self.lock:
            return self.conn.execute("""SELECT * FROM leads WHERE status NOT IN ('archiv','auftrag')
                AND checked_at IS NOT NULL AND score > 0 ORDER BY score DESC, checked_at DESC LIMIT ?""", (limit,)).fetchall()

    def get(self, lead_id):
        with self.lock:
            return self.conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()

    def status(self, lead_id, status):
        if status not in STATUS:
            return False
        with self.lock, self.conn:
            return self.conn.execute("UPDATE leads SET status=? WHERE id=?", (status, lead_id)).rowcount > 0

    def draft(self, lead_id, text):
        with self.lock, self.conn:
            self.conn.execute("UPDATE leads SET draft=? WHERE id=?", (text, lead_id))

    def count(self):
        with self.lock:
            return self.conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]


def discover(profile, radius, endpoint):
    if profile not in PROFILES:
        raise ValueError("Unbekanntes Branchenprofil")
    if not 1000 <= radius <= 50000:
        raise ValueError("Suchradius muss zwischen 1 und 50 km liegen")
    lat, lon = PASSAU
    clauses = "".join(f'nwr(around:{radius},{lat},{lon})["name"]["{key}"~"^({regex})$"];'
                      for key, regex in PROFILES[profile])
    query = f"[out:json][timeout:35];({clauses});out tags center;"
    response = requests.post(endpoint, data={"data": query}, timeout=55, headers={"User-Agent": USER_AGENT})
    response.raise_for_status()
    items = []
    for element in response.json().get("elements", []):
        tags = element.get("tags", {})
        website = tags.get("website") or tags.get("contact:website")
        website = website.strip() if website and website.strip() else None
        items.append({"source_id": f'osm:{element["type"]}:{element["id"]}',
                      "name": tags.get("name", "")[:200], "profile": profile,
                      "website": website, "source_url": f'https://www.openstreetmap.org/{element["type"]}/{element["id"]}',
                      "discovered_at": now()})
    return items


def draft_for(lead, result):
    """Create a short, verifiable conversation note without loading a local LLM.

    Website titles and descriptions are deliberately excluded: they often contain
    repetitive SEO terms and are untrusted content.
    """
    name = re.sub(r"\s+", " ", lead["name"]).strip()[:100]
    url = result.get("url") or lead["website"] or "Website ungeklärt"
    checked = (result.get("checked_at") or "Datum unbekannt")[:10]
    if result.get("error") or not result:
        return "Keine verlässliche Prüfung vorhanden. Website zuerst manuell ansehen."

    if not result.get("contact_link"):
        observation = "Auf der geprüften Startseite wurde kein direkter Kontaktlink erkannt."
        idea = "Kontakt und Anfrageweg sichtbar platzieren und auf dem Smartphone testen."
    elif not result.get("cta_link"):
        observation = "Auf der geprüften Startseite wurde kein direkter Termin- oder Anfragelink erkannt."
        idea = "Einen klaren Einstieg für Terminanfragen prüfen und gegebenenfalls vereinfachen."
    elif result.get("mobile_performance") is not None and result["mobile_performance"] < 50:
        observation = f'Die mobile PageSpeed-Prüfung ergab {result["mobile_performance"]}/100 Punkte.'
        idea = "Die größten Ladezeitbremsen prüfen und die mobile Startseite gezielt verbessern."
    elif not result.get("viewport"):
        observation = "Im HTML der geprüften Startseite wurde kein mobiler Viewport-Eintrag erkannt."
        idea = "Die Darstellung und Bedienung auf Smartphones prüfen."
    elif not result.get("description"):
        observation = "Im HTML der geprüften Startseite wurde keine Meta-Beschreibung erkannt."
        idea = "Seitentitel und Suchergebnis-Vorschau gemeinsam überarbeiten."
    elif not result.get("https"):
        observation = "Die geprüfte Startseite wurde über HTTP ausgeliefert."
        idea = "Die HTTPS-Auslieferung und Weiterleitungen prüfen."
    else:
        observation = "Die automatische Prüfung ergab keinen klar belegten Verbesserungsansatz."
        idea = "Website manuell prüfen, bevor ein Angebot formuliert wird."

    return (f"{name} – interne Gesprächsnotiz\n"
            f"Beobachtung ({checked}): {observation}\n"
            f"Orivan-Ansatz: {idea}\n"
            "Vor einer Ansprache den Befund auf der Website manuell bestätigen.\n"
            f"Geprüfte Seite: {url}")


class Engine:
    def __init__(self, db, config):
        self.db, self.config = db, config
        self.run_lock = threading.Lock()

    def run(self, profile="alle", notify=None):
        if not self.run_lock.acquire(blocking=False):
            return "Es läuft bereits eine Suche."
        try:
            profiles = list(PROFILES) if profile == "alle" else [profile]
            if any(p not in PROFILES for p in profiles):
                return "Branche: handwerk, praxis, dienstleister oder alle."
            added, errors = 0, []
            for p in profiles:
                try:
                    for item in discover(p, self.config["radius"], self.config["overpass_url"]):
                        added += self.db.add(item)
                except (requests.RequestException, ValueError, KeyError) as exc:
                    errors.append(f"{p}: {str(exc)[:100]}")
                time.sleep(2)
            checked = 0
            for lead in self.db.pending(self.config["max_audits"]):
                result = audit(lead["website"], self.config["pagespeed_key"])
                self.db.update_audit(lead["id"], result, score(lead["profile"], result))
                checked += 1
                time.sleep(1)
            self.db.set("last_run", now())
            summary = f"Suche beendet: {added} neue Unternehmen, {checked} Websites geprüft."
            if errors:
                summary += " Fehler: " + "; ".join(errors)
            if notify:
                notify(summary)
            return summary
        finally:
            self.run_lock.release()


def daily_due(db, config):
    if db.setting("schedule", "on") != "on":
        return False
    local = datetime.now(ZoneInfo(config["timezone"]))
    return local.hour >= config["hour"] and db.setting("daily_date") != local.date().isoformat()
