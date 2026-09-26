import ipaddress
import hashlib
import json
import logging
import os
import re
import socket
import sqlite3
import threading
import time
from datetime import datetime, time as daytime, timedelta, timezone
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
OVERPASS_FALLBACK = "https://overpass.private.coffee/api/interpreter"
STATUS = {"neu", "interessant", "kontaktiert", "antwort", "angebot", "auftrag", "archiv"}


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
    points = 30 if profile in PROFILES or profile == 'manuell' else 0
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
                CREATE TABLE IF NOT EXISTS lead_workflow (
                    lead_id INTEGER PRIMARY KEY, shown_on TEXT, follow_up_on TEXT,
                    note TEXT NOT NULL DEFAULT ''
                );
                INSERT OR IGNORE INTO lead_workflow(lead_id,follow_up_on)
                    SELECT id,date('now') FROM leads WHERE status IN ('kontaktiert','antwort','angebot');
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

    def add_manual(self, name, website, source):
        key = hashlib.sha256((name.strip().casefold() + '\n' + (website or '').strip().casefold()).encode()).hexdigest()
        source_id = 'manual:' + key
        self.add({'source_id': source_id, 'name': name[:200], 'profile': 'manuell',
                  'website': website, 'source_url': 'Manuell: ' + source[:200], 'discovered_at': now()})
        with self.lock:
            return self.conn.execute('SELECT id FROM leads WHERE source_id=?', (source_id,)).fetchone()[0]

    def pending(self, limit):
        with self.lock:
            return self.conn.execute("""SELECT * FROM leads WHERE website IS NOT NULL
                AND status NOT IN ('archiv','auftrag') AND
                (checked_at IS NULL OR (checked_at < datetime('now', '-30 days')
                AND json_extract(audit_json, '$.error') IS NULL) OR
                (checked_at < datetime('now', '-7 days')
                AND json_extract(audit_json, '$.error') IS NOT NULL))
                ORDER BY checked_at IS NOT NULL, source_id LIKE 'manual:%' DESC, checked_at ASC LIMIT ?""", (limit,)).fetchall()

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

    def status(self, lead_id, status, follow_up_on=None):
        if status not in STATUS:
            return False
        with self.lock, self.conn:
            lead = self.conn.execute("SELECT status FROM leads WHERE id=?", (lead_id,)).fetchone()
            if not lead:
                return False
            if lead["status"] == status:
                return True
            self.conn.execute("UPDATE leads SET status=? WHERE id=?", (status, lead_id))
            self.conn.execute("""INSERT INTO lead_workflow(lead_id,follow_up_on) VALUES (?,?)
                ON CONFLICT(lead_id) DO UPDATE SET follow_up_on=excluded.follow_up_on""",
                (lead_id, None if status in ('auftrag', 'archiv') else follow_up_on))
            return True

    def workflow(self, lead_id):
        with self.lock:
            return self.conn.execute("SELECT * FROM lead_workflow WHERE lead_id=?", (lead_id,)).fetchone()

    def set_followup(self, lead_id, day):
        with self.lock, self.conn:
            lead = self.conn.execute("SELECT status FROM leads WHERE id=?", (lead_id,)).fetchone()
            if not lead or lead["status"] in ('auftrag', 'archiv'):
                return False
            self.conn.execute("""INSERT INTO lead_workflow(lead_id,follow_up_on) VALUES (?,?)
                ON CONFLICT(lead_id) DO UPDATE SET follow_up_on=excluded.follow_up_on""", (lead_id, day))
            return True

    def set_note(self, lead_id, note):
        with self.lock, self.conn:
            if not self.conn.execute("SELECT id FROM leads WHERE id=?", (lead_id,)).fetchone():
                return False
            self.conn.execute("""INSERT INTO lead_workflow(lead_id,note) VALUES (?,?)
                ON CONFLICT(lead_id) DO UPDATE SET note=excluded.note""", (lead_id, note[:800]))
            return True

    def due_followups(self, day, limit=10):
        with self.lock:
            return self.conn.execute("""SELECT l.*, w.follow_up_on, w.note FROM leads l
                JOIN lead_workflow w ON w.lead_id=l.id
                WHERE w.follow_up_on<=? AND l.status NOT IN ('archiv','auftrag')
                ORDER BY w.follow_up_on, l.score DESC, l.id LIMIT ?""", (day, limit)).fetchall()

    def daily_candidates(self, day, limit=3):
        """Keep today's selection stable; rotate previously shown firms on later days."""
        with self.lock, self.conn:
            leads = self.conn.execute("""SELECT l.* FROM leads l
                LEFT JOIN lead_workflow w ON w.lead_id=l.id
                WHERE l.status IN ('neu','interessant') AND l.score>=40
                AND julianday(l.checked_at)>=julianday('now','-30 days')
                AND l.audit_json IS NOT NULL AND json_extract(l.audit_json,'$.error') IS NULL
                AND w.follow_up_on IS NULL
                ORDER BY CASE WHEN w.shown_on=? THEN 0 WHEN w.shown_on IS NULL THEN 1 ELSE 2 END,
                w.shown_on, l.score DESC, l.id LIMIT ?""", (day, limit)).fetchall()
            for lead in leads:
                self.conn.execute("""INSERT INTO lead_workflow(lead_id,shown_on) VALUES (?,?)
                    ON CONFLICT(lead_id) DO UPDATE SET shown_on=excluded.shown_on""", (lead["id"], day))
            return leads

    def pipeline(self):
        with self.lock:
            return dict(self.conn.execute("SELECT status, COUNT(*) FROM leads GROUP BY status").fetchall())

    def draft(self, lead_id, text):
        with self.lock, self.conn:
            self.conn.execute("UPDATE leads SET draft=? WHERE id=?", (text, lead_id))

    def count(self):
        with self.lock:
            return self.conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]

    def metrics_snapshot(self, timezone_name):
        local_day = datetime.now(ZoneInfo(timezone_name)).date()
        start = datetime.combine(local_day, daytime.min, ZoneInfo(timezone_name)).astimezone(timezone.utc).isoformat(timespec="seconds")
        end = datetime.combine(local_day + timedelta(days=1), daytime.min, ZoneInfo(timezone_name)).astimezone(timezone.utc).isoformat(timespec="seconds")
        with self.lock:
            groups = self.conn.execute(
                "SELECT status, profile, COUNT(*) AS amount FROM leads GROUP BY status, profile"
            ).fetchall()
            totals = self.conn.execute("""SELECT
                COUNT(*) AS leads,
                SUM(CASE WHEN score >= 70 AND status NOT IN ('archiv','auftrag') THEN 1 ELSE 0 END) AS priority,
                SUM(CASE WHEN website IS NULL THEN 1 ELSE 0 END) AS without_website,
                SUM(CASE WHEN draft IS NOT NULL THEN 1 ELSE 0 END) AS drafts,
                SUM(CASE WHEN discovered_at>=? AND discovered_at<? THEN 1 ELSE 0 END) AS new_today,
                SUM(CASE WHEN checked_at>=? AND checked_at<? THEN 1 ELSE 0 END) AS checked_today
                FROM leads""", (start, end, start, end)).fetchone()
            settings = dict(self.conn.execute("""SELECT key, value FROM settings WHERE key IN
                ('last_run','last_search_new','last_search_checked','last_search_errors')""").fetchall())
            companies = self.conn.execute("""SELECT id, name, profile, status, website, score
                FROM leads WHERE status != 'archiv'
                ORDER BY score DESC, checked_at DESC, id DESC LIMIT 100""").fetchall()
            settings["followups_due"] = self.conn.execute("""SELECT COUNT(*) FROM lead_workflow w
                JOIN leads l ON l.id=w.lead_id WHERE w.follow_up_on<=?
                AND l.status NOT IN ('archiv','auftrag')""", (local_day.isoformat(),)).fetchone()[0]
        return groups, totals, settings, companies


def discover(profile, radius, endpoint, fallback_endpoint=""):
    if profile not in PROFILES:
        raise ValueError("Unbekanntes Branchenprofil")
    if not 1000 <= radius <= 50000:
        raise ValueError("Suchradius muss zwischen 1 und 50 km liegen")
    lat, lon = PASSAU
    clauses = "".join(f'nwr(around:{radius},{lat},{lon})["name"]["{key}"~"^({regex})$"];'
                      for key, regex in PROFILES[profile])
    query = f"[out:json][timeout:35];({clauses});out tags center;"
    def fetch(url):
        return requests.post(url, data={"data": query}, timeout=55,
                             headers={"User-Agent": USER_AGENT})

    try:
        response = fetch(endpoint)
    except (requests.Timeout, requests.ConnectionError):
        if not fallback_endpoint or fallback_endpoint == endpoint:
            raise
        response = fetch(fallback_endpoint)
    else:
        if response.status_code in (502, 503, 504) and fallback_endpoint and fallback_endpoint != endpoint:
            response.close()
            response = fetch(fallback_endpoint)
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


def opportunity_for(result):
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
    return observation, idea


def draft_for(lead, result):
    """Create an evidence-based note without reusing untrusted SEO metadata."""
    name = re.sub(r"\s+", " ", lead["name"]).strip()[:100]
    url = result.get("url") or lead["website"] or "Website ungeklärt"
    checked = (result.get("checked_at") or "Datum unbekannt")[:10]
    if result.get("error") or not result:
        return "Keine verlässliche Prüfung vorhanden. Website zuerst manuell ansehen."
    observation, idea = opportunity_for(result)

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
                    for item in discover(p, self.config["radius"], self.config["overpass_url"],
                                         self.config.get("overpass_fallback_url", OVERPASS_FALLBACK)):
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
            self.db.set("last_search_new", added)
            self.db.set("last_search_checked", checked)
            self.db.set("last_search_errors", len(errors))
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
