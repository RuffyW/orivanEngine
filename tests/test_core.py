import json
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

from orivan_engine.core import Database, Engine, SafeSession, SiteParser, discover, draft_for, public_url, score
from orivan_engine.__main__ import Bot


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


if __name__ == "__main__":
    unittest.main()
