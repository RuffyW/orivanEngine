import json
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

import requests

from orivan_engine.core import Database, Engine, SafeSession, SiteParser, discover, draft_for, public_url, score
from orivan_engine.__main__ import Bot
from orivan_engine.metrics import render_metrics


class CoreTests(unittest.TestCase):
    def test_public_url_rejects_private_addresses(self):
        self.assertIsNone(public_url("http://127.0.0.1/admin"))
        self.assertIsNone(public_url("http://169.254.169.254/latest/meta-data"))
        self.assertIsNone(public_url("file:///etc/passwd"))

    def test_redirects_are_not_followed_implicitly(self):
        response = Mock(is_redirect=False)
        with patch("orivan_engine.core.public_url", return_value="https://example.com"), \
             patch("requests.Session.request", return_value=response) as request:
            SafeSession().get("https://example.com")
        self.assertFalse(request.call_args.kwargs["allow_redirects"])

    def test_html_evidence_and_priority(self):
        parser = SiteParser()
        parser.feed('<html><head><title>Beispiel</title><meta name="viewport" content="width=device-width"></head>'
                    '<body><h1>Hallo</h1><a href="tel:123">Anrufen</a></body></html>')
        found = parser.findings()
        self.assertTrue(found["contact_link"])
        self.assertFalse(found["cta_link"])
        self.assertEqual(score("handwerk", {**found, "https": True}), 45)

    def test_discovery_and_deduplication(self):
        fake = Mock()
        fake.json.return_value = {"elements": [{"type": "node", "id": 9,
                                                  "tags": {"name": "Beispielbetrieb", "craft": "plumber",
                                                           "contact:website": "https://beispiel.de"}}]}
        with patch("orivan_engine.core.requests.post", return_value=fake):
            results = discover("handwerk", 20000, "https://example.org/api")
        self.assertEqual(results[0]["source_id"], "osm:node:9")
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(os.path.join(tmp, "leads.db"))
            self.assertEqual(db.add(results[0]), 1)
            self.assertEqual(db.add(results[0]), 0)
            lead = db.pending(1)[0]
            db.update_audit(lead["id"], {"url": "https://beispiel.de"}, 73)
            self.assertEqual(db.list()[0]["score"], 73)
            self.assertTrue(db.status(lead["id"], "interessant"))

    def test_discovery_uses_one_fallback_after_gateway_timeout(self):
        unavailable = Mock(status_code=504)
        fallback = Mock(status_code=200)
        fallback.json.return_value = {"elements": [{"type": "node", "id": 9,
                                                       "tags": {"name": "Praxis Beispiel"}}]}
        with patch("orivan_engine.core.requests.post", side_effect=[unavailable, fallback]) as post:
            leads = discover("praxis", 20000, "https://primary.example/api/interpreter",
                             "https://fallback.example/api/interpreter")
        self.assertEqual(len(leads), 1)
        self.assertEqual(post.call_count, 2)
        self.assertEqual(post.call_args.args[0], "https://fallback.example/api/interpreter")
        unavailable.close.assert_called_once()

    def test_discovery_does_not_retry_rate_limit(self):
        response = Mock(status_code=429)
        response.raise_for_status.side_effect = requests.HTTPError("429 Too Many Requests")
        with patch("orivan_engine.core.requests.post", return_value=response) as post:
            with self.assertRaises(requests.HTTPError):
                discover("praxis", 20000, "https://primary.example/api/interpreter",
                         "https://fallback.example/api/interpreter")
        post.assert_called_once()

    def test_search_runs_with_mocked_source_and_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(os.path.join(tmp, "leads.db"))
            config = {"radius": 20000, "overpass_url": "https://source.invalid",
                      "max_audits": 1, "pagespeed_key": ""}
            lead = {"source_id": "osm:node:1", "name": "Firma", "profile": "praxis",
                    "website": "https://example.com", "source_url": "https://openstreetmap.org/node/1",
                    "discovered_at": "2026-09-25"}
            with patch("orivan_engine.core.discover", return_value=[lead]), \
                 patch("orivan_engine.core.audit", return_value={"https": True, "title": "Firma",
                       "viewport": True, "description": "Text", "h1": True,
                       "contact_link": True, "cta_link": False}), \
                 patch("orivan_engine.core.time.sleep"):
                result = Engine(db, config).run("praxis")
            self.assertIn("1 neue Unternehmen, 1 Websites geprüft", result)
            self.assertEqual(db.list()[0]["score"], 40)

    def test_telegram_commands_only_for_allowed_private_user(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = {"db": os.path.join(tmp, "leads.db"), "token": "test", "allowed": {123},
                      "hour": 8, "timezone": "Europe/Berlin", "radius": 20000, "max_audits": 20}
            bot = Bot(config)
            bot.send = Mock()
            bot.command(456, 456, "/plan pausieren")
            self.assertEqual(bot.db.setting("schedule", "on"), "on")
            bot.command(123, -999, "/plan pausieren")
            self.assertEqual(bot.db.setting("schedule", "on"), "on")
            bot.command(123, 123, "/plan pausieren")
            self.assertEqual(bot.db.setting("schedule"), "off")

    def test_draft_ignores_repetitive_metadata_without_model_call(self):
        lead = {"name": "Elektro Gerner", "website": "https://example.com", "profile": "handwerk"}
        result = {"url": "https://example.com", "checked_at": "2026-09-26T12:00:00Z",
                  "title": "Elektroinstallation Jaegerwirth, " * 50,
                  "description": "Elektroinstallation Jaegerwirth, " * 50,
                  "contact_link": True, "cta_link": False, "https": True}
        with patch("orivan_engine.core.requests.post", side_effect=AssertionError("model request")):
            draft = draft_for(lead, result)
        self.assertIn("kein direkter Termin- oder Anfragelink erkannt", draft)
        self.assertIn("Geprüfte Seite: https://example.com", draft)
        self.assertNotIn("Jaegerwirth", draft)
        self.assertLess(len(draft), 500)

    def test_old_model_draft_is_cleared_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "leads.db")
            db = Database(path)
            db.add({"source_id": "osm:node:2", "name": "Elektro Gerner", "profile": "handwerk",
                    "website": "https://example.com", "source_url": "https://openstreetmap.org/node/2",
                    "discovered_at": "2026-09-25"})
            db.draft(1, "alter wiederholter Modelltext")
            db.set("draft_version", "1")
            db.conn.close()
            upgraded = Database(path)
            self.assertIsNone(upgraded.get(1)["draft"])
            upgraded.draft(1, "neue Gesprächsnotiz")
            upgraded.conn.close()
            self.assertEqual(Database(path).get(1)["draft"], "neue Gesprächsnotiz")

    def test_metrics_export_bounded_company_table_and_search_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(os.path.join(tmp, "leads.db"))
            config = {"radius": 20000, "overpass_url": "https://source.invalid",
                      "max_audits": 1, "pagespeed_key": ""}
            lead = {"source_id": "osm:node:1", "name": 'Gerner "Elektro" \\ Betrieb',
                    "profile": "praxis", "website": "https://example.com",
                    "source_url": "https://openstreetmap.org/node/1", "discovered_at": "2026-09-25"}
            engine = Engine(db, config)
            with patch("orivan_engine.core.discover", return_value=[lead]), \
                 patch("orivan_engine.core.audit", return_value={"https": True}), \
                 patch("orivan_engine.core.time.sleep"):
                engine.run("praxis")
            body = render_metrics(db, "Europe/Berlin", engine).decode()
            self.assertIn("orivan_leads 1\n", body)
            self.assertIn('orivan_leads_by_status_profile{status="neu",profile="praxis"} 1', body)
            self.assertIn("orivan_last_search_checked 1\n", body)
            self.assertIn('name="Gerner \\"Elektro\\" \\\\ Betrieb"', body)
            self.assertIn('website="https://example.com"', body)
            self.assertIn("orivan_company_priority{", body)

    def test_metrics_limit_company_series_on_large_discovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(os.path.join(tmp, "leads.db"))
            for lead_id in range(120):
                db.add({"source_id": f"osm:node:{lead_id}", "name": f"Firma {lead_id}",
                        "profile": "handwerk", "website": None,
                        "source_url": f"https://www.openstreetmap.org/node/{lead_id}",
                        "discovered_at": "2026-09-25"})
            engine = Engine(db, {})
            body = render_metrics(db, "Europe/Berlin", engine).decode()
            self.assertEqual(body.count("\norivan_company_priority{"), 100)
            self.assertIn('lead_id="119"', body)
            self.assertNotIn('lead_id="1"', body)


if __name__ == "__main__":
    unittest.main()
