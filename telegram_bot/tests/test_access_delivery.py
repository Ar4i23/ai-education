import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from integrations.access_delivery import (
    init_delivery_table,
    enqueue_delivery,
    get_pending_deliveries,
    claim_next_delivery,
    save_delivery_invite,
    mark_delivery_success,
    mark_delivery_failure,
)


class AccessDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.db"

        def test_connection():
            connection = sqlite3.connect(self.db_path)
            connection.execute("PRAGMA foreign_keys = ON")
            return connection

        self.connect = test_connection
        self.connection_patch = patch(
            "integrations.access_delivery.get_connection",
            side_effect=test_connection,
        )
        self.connection_patch.start()

        with test_connection() as connection:
            connection.executescript("""
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY,
                    telegram_id INTEGER NOT NULL
                );
                CREATE TABLE orders (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    status TEXT NOT NULL
                );
                INSERT INTO users VALUES (1, 123456789);
                INSERT INTO orders VALUES (1, 1, 'paid');
                INSERT INTO orders VALUES (2, 1, 'pending');
            """)
        init_delivery_table()

    def tearDown(self):
        self.connection_patch.stop()
        self.temp_dir.cleanup()

    def test_paid_order_is_enqueued(self):
        self.assertTrue(enqueue_delivery(1, 123456789))
        tasks = get_pending_deliveries()
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["order_id"], 1)

    def test_pending_order_is_rejected(self):
        self.assertFalse(enqueue_delivery(2, 123456789))
        self.assertEqual(get_pending_deliveries(), [])

    def test_duplicate_delivery_is_prevented(self):
        self.assertTrue(enqueue_delivery(1, 123456789))
        self.assertFalse(enqueue_delivery(1, 123456789))
        self.assertEqual(len(get_pending_deliveries()), 1)

    def test_failure_increases_attempts(self):
        enqueue_delivery(1, 123456789)
        task = claim_next_delivery()
        self.assertTrue(mark_delivery_failure(
            1, task["claimed_at"], task["claim_token"], "Telegram unavailable"
        ))
        tasks = get_pending_deliveries()
        self.assertEqual(tasks[0]["attempts"], 1)

    def test_success_removes_pending_task(self):
        enqueue_delivery(1, 123456789)
        task = claim_next_delivery()
        expires_at = (
            datetime.now(timezone.utc) + timedelta(hours=24)
        ).isoformat()
        self.assertTrue(save_delivery_invite(
            1, task["claimed_at"], task["claim_token"],
            "https://t.me/+test", expires_at
        ))
        self.assertTrue(mark_delivery_success(
            1, task["claimed_at"], task["claim_token"], "https://t.me/+test"
        ))
        self.assertEqual(get_pending_deliveries(), [])

    def test_wrong_token_cannot_mark_success(self):
        enqueue_delivery(1, 123456789)
        task = claim_next_delivery()
        expires_at = (
            datetime.now(timezone.utc) + timedelta(hours=24)
        ).isoformat()
        self.assertTrue(save_delivery_invite(
            1, task["claimed_at"], task["claim_token"],
            "https://t.me/+test", expires_at
        ))
        self.assertFalse(mark_delivery_success(
            1, task["claimed_at"], "wrong-token", "https://t.me/+test"
        ))
        with self.connect() as connection:
            status = connection.execute(
                "SELECT status FROM access_deliveries WHERE order_id = 1"
            ).fetchone()[0]
        self.assertEqual(status, "pending")


if __name__ == "__main__":
    unittest.main()
