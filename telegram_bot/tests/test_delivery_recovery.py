import sqlite3
import tempfile
import unittest

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from integrations.access_delivery import (
    init_delivery_table,
    recover_stale_deliveries,
)


class DeliveryRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.db"

        def connect():
            connection = sqlite3.connect(self.db_path)
            connection.execute("PRAGMA foreign_keys = ON")
            return connection

        self.connect = connect

        self.patcher = patch(
            "integrations.access_delivery.get_connection",
            side_effect=self.connect,
        )
        self.patcher.start()

        with self.connect() as connection:
            connection.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY)")
            connection.executemany(
                "INSERT INTO orders(id) VALUES (?)",
                [(1,), (2,), (3,)],
            )

        init_delivery_table()

        now = datetime.now(timezone.utc)

        old_claim = (now - timedelta(minutes=20)).isoformat()

        recent_claim = (now - timedelta(minutes=1)).isoformat()

        with self.connect() as connection:
            connection.executemany(
                """
                INSERT INTO access_deliveries (
                    order_id,
                    telegram_id,
                    status,
                    claimed_at
                )
                VALUES (?, ?, ?, ?)
                """,
                [
                    (1, 111, "pending", old_claim),
                    (2, 222, "pending", recent_claim),
                    (3, 333, "delivered", old_claim),
                ],
            )

    def tearDown(self):
        self.patcher.stop()
        self.temp_dir.cleanup()

    def test_recovers_only_old_pending_claim(self):
        recovered = recover_stale_deliveries(timeout_minutes=10)

        self.assertEqual(recovered, 1)

        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT order_id, claimed_at
                FROM access_deliveries
                ORDER BY order_id
                """
            ).fetchall()

        self.assertIsNone(rows[0][1])
        self.assertIsNotNone(rows[1][1])
        self.assertIsNotNone(rows[2][1])

    def test_recovery_is_idempotent(self):
        first = recover_stale_deliveries(10)
        second = recover_stale_deliveries(10)

        self.assertEqual(first, 1)
        self.assertEqual(second, 0)

    def test_rejects_invalid_timeout(self):
        with self.assertRaises(ValueError):
            recover_stale_deliveries(0)


if __name__ == "__main__":
    unittest.main()
