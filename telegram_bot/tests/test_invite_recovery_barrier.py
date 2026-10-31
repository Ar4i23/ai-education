import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from integrations.access_delivery import (
    init_delivery_table, claim_next_delivery, begin_invite_creation,
    recover_stale_deliveries, release_delivery,
)
from integrations.delivery_worker import process_one_delivery


class InviteRecoveryBarrierTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / 'test.db'
        def connect():
            return sqlite3.connect(self.db)
        self.connect = connect
        self.patcher = patch('integrations.access_delivery.get_connection', side_effect=connect)
        self.patcher.start()
        with connect() as c:
            c.execute('CREATE TABLE orders (id INTEGER PRIMARY KEY)')
            c.execute('INSERT INTO orders VALUES (1)')
        init_delivery_table()
        with connect() as c:
            c.execute('INSERT INTO access_deliveries(order_id, telegram_id) VALUES (1, 123)')

    def tearDown(self):
        self.patcher.stop()
        self.tmp.cleanup()

    def make_stale(self):
        old = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        with self.connect() as c:
            c.execute('UPDATE access_deliveries SET claimed_at = ? WHERE order_id = 1', (old,))

    async def test_stale_before_creation_can_recover(self):
        claim = claim_next_delivery()
        self.make_stale()
        self.assertEqual(recover_stale_deliveries(10), 1)
        self.assertIsNotNone(claim_next_delivery())

    async def test_stale_after_creation_start_never_recovers(self):
        claim = claim_next_delivery()
        self.assertTrue(begin_invite_creation(1, claim['claimed_at'], claim['claim_token']))
        self.make_stale()
        self.assertEqual(recover_stale_deliveries(10), 0)
        self.assertIsNone(claim_next_delivery())
        self.assertFalse(release_delivery(1, claim['claimed_at'], claim['claim_token']))

    async def test_telegram_failure_cannot_be_retried_after_recovery(self):
        with patch('integrations.delivery_worker.create_course_invite_with_expiry',
                   new_callable=AsyncMock, side_effect=RuntimeError('lost response')) as create:
            self.assertEqual(await process_one_delivery(object(), -100123), 'needs_review')
            self.make_stale()
            self.assertEqual(recover_stale_deliveries(10), 0)
            self.assertEqual(await process_one_delivery(object(), -100123), 'empty')
            create.assert_awaited_once()

    async def test_old_schema_migrates(self):
        with self.connect() as c:
            columns = {r[1] for r in c.execute('PRAGMA table_info(access_deliveries)')}
        self.assertIn('invite_started_at', columns)
        init_delivery_table()
