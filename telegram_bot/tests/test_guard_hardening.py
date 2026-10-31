"""SQLite integration tests for course join guards; no Telegram network calls."""

import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import database
from integrations.access_delivery import init_delivery_table
from integrations.access_recovery import init_recovery_table
from integrations.join_claims import init_join_claims_table, reserve_join, change_join_status

CHAT = -1004307729335
BUYER = 111111
STRANGER = 222222
LINK = 'https://t.me/+guard-real-db-test'
RECOVERY_LINK = 'https://t.me/+guard-recovery-real-db-test'


class GuardDatabaseIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'education.db'
        self.path_patch = patch.object(database, 'DATABASE_PATH', self.path)
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)
        database.init_database()
        init_delivery_table()
        init_recovery_table()
        init_join_claims_table()
        database.save_user(BUYER, None, 'Buyer')
        database.save_user(STRANGER, None, 'Stranger')
        self.order_id = database.create_order(BUYER, '3499', 'RUB')
        self.future = (datetime.now(timezone.utc) + timedelta(hours=12)).isoformat()
        with database.get_connection() as db:
            db.execute("UPDATE orders SET status = 'paid' WHERE id = ?", (self.order_id,))
            db.execute("""INSERT INTO access_deliveries (
                order_id, telegram_id, status, invite_link, invite_expires_at
            ) VALUES (?, ?, 'delivered', ?, ?)""",
                (self.order_id, BUYER, LINK, self.future))
            db.execute("""INSERT INTO access_recoveries (
                telegram_id, order_id, status, token, invite_link,
                invite_expires_at, created_at, updated_at
            ) VALUES (?, ?, 'delivered', 'test-unique-token', ?, ?, ?, ?)""",
                (BUYER, self.order_id, RECOVERY_LINK, self.future, self.future, self.future))

    def reserve(self, link=LINK, user=BUYER, chat=CHAT):
        return reserve_join(link, user, chat, CHAT)

    def test_actual_schema_delivery_approval(self):
        self.assertEqual(self.reserve(), 'reserved')
        with database.get_connection() as db:
            row = db.execute('SELECT source_type, source_id FROM course_join_claims').fetchone()
        self.assertEqual(row, ('delivery', self.order_id))

    def test_actual_schema_recovery_approval(self):
        self.assertEqual(self.reserve(RECOVERY_LINK), 'reserved')
        with database.get_connection() as db:
            source = db.execute('SELECT source_type FROM course_join_claims').fetchone()[0]
        self.assertEqual(source, 'recovery')

    def test_stranger_cannot_reserve_paid_link(self):
        self.assertEqual(self.reserve(user=STRANGER), 'deny')
        self.assertEqual(self.reserve(), 'reserved')

    def test_recovery_stranger_cannot_reserve(self):
        self.assertEqual(self.reserve(RECOVERY_LINK, STRANGER), 'deny')

    def test_payment_revoked_in_database(self):
        with database.get_connection() as db:
            db.execute("UPDATE orders SET status = 'pending' WHERE id = ?", (self.order_id,))
        self.assertEqual(self.reserve(), 'deny')

    def test_expiry_blocks_approval(self):
        with database.get_connection() as db:
            db.execute('UPDATE access_deliveries SET invite_expires_at = ?',
                       ((datetime.now(timezone.utc)-timedelta(seconds=5)).isoformat(),))
        self.assertEqual(self.reserve(), 'deny')

    def test_broken_expiry_blocks_approval(self):
        with database.get_connection() as db:
            db.execute('UPDATE access_deliveries SET invite_expires_at = ?', ('broken',))
        self.assertEqual(self.reserve(), 'deny')

    def test_wrong_chat_is_ignored(self):
        self.assertEqual(self.reserve(chat=CHAT-1), 'ignore')

    def test_unknown_link_is_not_managed_by_bot(self):
        self.assertEqual(self.reserve(link='https://t.me/+wife-manual'), 'ignore')

    def test_duplicate_link_across_delivery_and_recovery_fails_closed(self):
        with database.get_connection() as db:
            db.execute('UPDATE access_recoveries SET invite_link = ?', (LINK,))
        self.assertEqual(self.reserve(), 'review')
        with database.get_connection() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM course_join_claims').fetchone()[0], 0)

    def test_duplicate_link_within_delivery_fails_closed(self):
        second = database.create_order(STRANGER, '3499', 'RUB')
        with database.get_connection() as db:
            db.execute("UPDATE orders SET status = 'paid' WHERE id = ?", (second,))
            db.execute("""INSERT INTO access_deliveries (
                order_id, telegram_id, status, invite_link, invite_expires_at
            ) VALUES (?, ?, 'delivered', ?, ?)""", (second, STRANGER, LINK, self.future))
        self.assertEqual(self.reserve(), 'review')

    def test_once_reserved_cannot_auto_retry(self):
        self.assertEqual(self.reserve(), 'reserved')
        self.assertEqual(self.reserve(), 'review')
        self.assertTrue(change_join_status(LINK, BUYER, 'approving', 'needs_review'))
        self.assertEqual(self.reserve(), 'review')

    def test_compare_and_swap_audit(self):
        self.assertEqual(self.reserve(), 'reserved')
        self.assertTrue(change_join_status(LINK, BUYER, 'approving', 'approved'))
        self.assertTrue(change_join_status(LINK, BUYER, 'approved', 'completed'))
        self.assertFalse(change_join_status(LINK, BUYER, 'approved', 'completed'))
        with database.get_connection() as db:
            row = db.execute('SELECT status, approved_at, completed_at FROM course_join_claims').fetchone()
        self.assertEqual(row[0], 'completed')
        self.assertTrue(row[1] and row[2])

    def test_atomic_parallel_reservation_real_schema(self):
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(lambda _: self.reserve(), range(3)))
        self.assertEqual(results.count('reserved'), 1)
        self.assertEqual(results.count('review'), 2)

    def test_idempotent_schema_init_preserves_records(self):
        self.assertEqual(self.reserve(), 'reserved')
        init_join_claims_table()
        init_join_claims_table()
        with database.get_connection() as db:
            self.assertEqual(db.execute('SELECT status FROM course_join_claims').fetchone()[0], 'approving')

    def test_legacy_claims_migration_preserves_records(self):
        with database.get_connection() as db:
            db.execute('DROP TABLE course_join_claims')
            db.execute("""CREATE TABLE course_join_claims (
                invite_link TEXT PRIMARY KEY, telegram_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'approving',
                claimed_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )""")
            db.execute("""INSERT INTO course_join_claims VALUES
                ('old-link', 111111, -1004307729335, 'needs_review', 'old', 'old')""")
        init_join_claims_table()
        with database.get_connection() as db:
            fields = [r[1] for r in db.execute('PRAGMA table_info(course_join_claims)')]
            old = db.execute('SELECT status FROM course_join_claims WHERE invite_link = ?', ('old-link',)).fetchone()
        self.assertIn('last_error', fields)
        self.assertIn('completed_at', fields)
        self.assertEqual(old[0], 'needs_review')

    def test_recovery_init_idempotent_preserves_record(self):
        init_recovery_table()
        with database.get_connection() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM access_recoveries').fetchone()[0], 1)

    def test_status_error_is_persisted(self):
        self.assertEqual(self.reserve(), 'reserved')
        self.assertTrue(change_join_status(LINK, BUYER, 'approving', 'needs_review', error='telegram_timeout'))
        with database.get_connection() as db:
            result = db.execute('SELECT last_error FROM course_join_claims').fetchone()[0]
        self.assertEqual(result, 'telegram_timeout')


if __name__ == '__main__':
    unittest.main()
