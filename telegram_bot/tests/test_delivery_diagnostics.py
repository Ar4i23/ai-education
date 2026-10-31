import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from integrations.delivery_diagnostics import classify_delivery, inspect_deliveries


class DeliveryDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'test.db'
        with sqlite3.connect(self.path) as conn:
            conn.execute('''CREATE TABLE access_deliveries (
                order_id INTEGER, telegram_id INTEGER, status TEXT,
                attempts INTEGER, last_error TEXT, claimed_at TEXT,
                claim_token TEXT, invite_link TEXT, invite_expires_at TEXT,
                created_at TEXT, delivered_at TEXT
            )''')

    def tearDown(self):
        self.temp.cleanup()

    def insert(self, *, order_id=1, status='pending', claimed_at=None,
               claim_token=None, invite_link=None):
        with sqlite3.connect(self.path) as conn:
            conn.execute('''INSERT INTO access_deliveries VALUES
                (?,123,?,0,NULL,?,?,?,NULL,CURRENT_TIMESTAMP,NULL)''',
                (order_id, status, claimed_at, claim_token, invite_link))

    def test_empty_database(self):
        self.assertEqual(inspect_deliveries(self.path), [])

    def test_saved_invite_needs_review_and_is_not_exposed(self):
        self.insert(invite_link='https://t.me/+private')
        rows = inspect_deliveries(self.path)
        self.assertEqual(rows[0]['diagnosis'], 'review_saved_invite')
        self.assertNotIn('invite_link', rows[0])
        self.assertTrue(rows[0]['has_invite'])

    def test_ready_is_excluded_by_default(self):
        self.insert()
        self.assertEqual(inspect_deliveries(self.path), [])
        self.assertEqual(inspect_deliveries(self.path, only_review=False)[0]['diagnosis'], 'ready')

    def test_stale_claim_is_flagged(self):
        old = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
        self.insert(claimed_at=old, claim_token='abc')
        self.assertEqual(inspect_deliveries(self.path)[0]['diagnosis'], 'review_stale_claim')

    def test_recent_claim_is_processing(self):
        current = datetime.now(timezone.utc).isoformat()
        self.insert(claimed_at=current, claim_token='abc')
        self.assertEqual(inspect_deliveries(self.path, only_review=False)[0]['diagnosis'], 'processing')

    def test_inconsistent_claim_is_flagged(self):
        self.insert(claim_token='abc')
        self.assertEqual(inspect_deliveries(self.path)[0]['diagnosis'], 'review_inconsistent_claim')

    def test_delivered_is_not_review(self):
        self.insert(status='delivered')
        self.assertEqual(inspect_deliveries(self.path), [])

    def test_diagnostics_do_not_modify_database(self):
        self.insert(invite_link='https://t.me/+private')
        with sqlite3.connect(self.path) as conn:
            before = conn.execute('SELECT * FROM access_deliveries').fetchall()
        inspect_deliveries(self.path)
        with sqlite3.connect(self.path) as conn:
            after = conn.execute('SELECT * FROM access_deliveries').fetchall()
        self.assertEqual(before, after)

    def test_missing_database_not_created(self):
        missing = Path(self.temp.name) / 'missing.db'
        with self.assertRaises(FileNotFoundError):
            inspect_deliveries(missing)
        self.assertFalse(missing.exists())


if __name__ == '__main__':
    unittest.main()
