import json
import os
import tempfile
import unittest
from datetime import timedelta
from unittest.mock import Mock, patch

from orivan_engine.__main__ import Bot
from orivan_engine.core import Database, now
from orivan_engine.metrics import render_metrics
from orivan_engine.sales import acquisition_pack, marketing_draft


class SalesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, 'leads.db')
        self.bot = Bot({'db': self.path, 'token': 'test', 'allowed': {123},
                        'timezone': 'Europe/Berlin', 'hour': 0, 'daily_leads': 3})
        self.bot.send = Mock()
        self.db = self.bot.db
        self.day = self.bot.today().isoformat()

    def tearDown(self):
        self.db.conn.close()
        self.tmp.cleanup()

    def lead(self, number, priority=70, status='neu', audited=True):
        self.db.add({'source_id': f'osm:node:{number}', 'name': f'Firma {number}',
                     'profile': 'handwerk', 'website': f'https://firma{number}.example',
                     'source_url': f'https://www.openstreetmap.org/node/{number}',
                     'discovered_at': now()})
        with self.db.lock:
            lead_id = self.db.conn.execute('SELECT id FROM leads WHERE source_id=?',
                                           (f'osm:node:{number}',)).fetchone()[0]
        if audited:
            self.db.update_audit(lead_id, {'https': True, 'contact_link': False,
                                         'checked_at': now(), 'title': 'SEO spam ' * 100}, priority)
        self.db.status(lead_id, status)
        return lead_id

    def test_today_stays_stable_rotates_and_excludes_existing_conversations(self):
        ids = [self.lead(i, 80-i) for i in range(5)]
        self.lead(10, 100, 'kontaktiert')
        self.lead(11, 100, 'archiv')
        self.lead(12, 100, audited=False)
        self.db.set_followup(ids[4], (self.bot.today()+timedelta(days=7)).isoformat())
        first = [row['id'] for row in self.db.daily_candidates(self.day)]
        self.assertEqual(first, ids[:3])
        self.assertEqual(first, [row['id'] for row in self.db.daily_candidates(self.day)])
        tomorrow = (self.bot.today()+timedelta(days=1)).isoformat()
        self.assertEqual(self.db.daily_candidates(tomorrow)[0]['id'], ids[3])

    def test_followup_status_buttons_are_idempotent_and_close_reminders(self):
        lead_id = self.lead(1)
        self.bot.mark_status(123, lead_id, 'kontaktiert')
        first_due = self.db.workflow(lead_id)['follow_up_on']
        with patch.object(self.bot, 'today', return_value=self.bot.today()+timedelta(days=1)):
            self.bot.mark_status(123, lead_id, 'kontaktiert')
        self.assertEqual(first_due, self.db.workflow(lead_id)['follow_up_on'])
        self.bot.mark_status(123, lead_id, 'antwort')
        self.assertEqual(len(self.db.due_followups(self.day)), 1)
        self.db.set_note(lead_id, 'Private Gesprächsnotiz')
        metrics = render_metrics(self.db, 'Europe/Berlin', self.bot.engine).decode()
        self.assertIn('orivan_followups_due 1\n', metrics)
        self.assertNotIn('Private Gesprächsnotiz', metrics)
        self.bot.mark_status(123, lead_id, 'auftrag')
        self.assertEqual(self.db.due_followups(self.day), [])
        self.assertIsNone(self.db.workflow(lead_id)['follow_up_on'])
        self.assertFalse(self.db.set_followup(lead_id, self.day))

    def test_migration_preserves_lead_and_sets_review_for_legacy_conversation(self):
        lead_id = self.lead(1, status='kontaktiert')
        self.db.draft(lead_id, 'Bestehende Notiz')
        with self.db.conn:
            self.db.conn.execute('DROP TABLE lead_workflow')
        self.db.conn.close()
        self.db = Database(self.path)
        self.assertEqual(self.db.get(lead_id)['status'], 'kontaktiert')
        self.assertEqual(self.db.get(lead_id)['draft'], 'Bestehende Notiz')
        self.assertEqual(len(self.db.due_followups(self.day)), 1)
        self.db.set_followup(lead_id, None)
        self.db.conn.close()
        self.db = Database(self.path)
        self.assertEqual(self.db.due_followups(self.day), [])

    def test_manual_lead_and_note_are_local_and_authorized(self):
        command = '/firma Muster GmbH | muster.example | Empfehlung von Partner'
        with patch('orivan_engine.core.requests.post', side_effect=AssertionError('external call')):
            self.bot.command(999, 999, command)
            self.assertEqual(self.db.count(), 0)
            self.bot.command(123, 123, command)
            self.bot.command(123, 123, command)
            self.assertEqual(self.db.count(), 1)
            self.bot.command(123, 123, '/notiz 1 Rückruf mit Frau Müller vereinbart')
            self.assertEqual(self.db.workflow(1)['note'], 'Rückruf mit Frau Müller vereinbart')
            self.bot.command(123, 123, '/wiedervorlage 1 7')
            self.assertEqual(self.db.due_followups(self.day), [])
            self.bot.command(123, 123, '/wiedervorlage 1 aus')
            self.assertIsNone(self.db.workflow(1)['follow_up_on'])

    def test_pack_does_not_repeat_metadata_or_need_external_service(self):
        lead_id = self.lead(1)
        with patch('orivan_engine.core.requests.post', side_effect=AssertionError('external call')):
            text = acquisition_pack(self.db.get(lead_id))
            marketing = marketing_draft(self.day)
        self.assertNotIn('SEO spam', text)
        self.assertIn('Gesprächsfragen', text)
        self.assertIn('CHECK', marketing)
        self.assertLess(len(text), 4000)
        self.db.update_audit(lead_id, {'error': 'Timeout'}, 0)
        self.assertNotIn('Textvorschlag', acquisition_pack(self.db.get(lead_id)))

    def test_busy_worker_does_not_consume_scheduled_run(self):
        self.bot.worker = Mock()
        self.bot.worker.is_alive.return_value = True
        self.bot.scheduled_tick()
        self.assertIsNone(self.db.setting('daily_date'))
        self.bot.send.assert_not_called()
        with patch.object(self.bot, 'launch', return_value=True):
            self.bot.scheduled_tick()
        self.assertEqual(self.db.setting('daily_date'), self.day)


if __name__ == '__main__':
    unittest.main()
