import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from integrations.access_delivery import (
    init_delivery_table,
    claim_next_delivery,
    release_delivery,
)


class DeliveryClaimTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.db"

        def connect():
            connection = sqlite3.connect(self.db_path, timeout=10)
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
            connection.execute("INSERT INTO orders(id) VALUES (1)")

        init_delivery_table()
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO access_deliveries (order_id, telegram_id) "
                "VALUES (1, 123456789)"
            )

    def tearDown(self):
        self.patcher.stop()
        self.temp_dir.cleanup()

    def test_claim_prevents_second_claim(self):
        first = claim_next_delivery()
        second = claim_next_delivery()
        self.assertIsNotNone(first)
        self.assertEqual(first["order_id"], 1)
        self.assertTrue(first["claim_token"])
        self.assertIsNone(second)

    def test_release_allows_retry(self):
        first = claim_next_delivery()
        self.assertTrue(release_delivery(
            order_id=1,
            claimed_at=first["claimed_at"],
            claim_token=first["claim_token"],
            error="Temporary Telegram error",
        ))
        second = claim_next_delivery()
        self.assertIsNotNone(second)
        self.assertEqual(second["order_id"], 1)
        self.assertNotEqual(first["claim_token"], second["claim_token"])

        with self.connect() as connection:
            row = connection.execute(
                "SELECT attempts, last_error FROM access_deliveries "
                "WHERE order_id = 1"
            ).fetchone()
        self.assertEqual(row[0], 1)
        self.assertEqual(row[1], "Temporary Telegram error")

    def test_wrong_token_cannot_release_claim(self):
        first = claim_next_delivery()
        self.assertFalse(release_delivery(
            1, first["claimed_at"], "wrong-token", "Not my claim"
        ))
        self.assertIsNone(claim_next_delivery())
        with self.connect() as connection:
            row = connection.execute(
                "SELECT claim_token, attempts FROM access_deliveries "
                "WHERE order_id = 1"
            ).fetchone()
        self.assertEqual(row, (first["claim_token"], 0))

    def test_old_worker_cannot_release_new_claim(self):
        first = claim_next_delivery()
        self.assertTrue(release_delivery(
            1, first["claimed_at"], first["claim_token"]
        ))
        second = claim_next_delivery()
        self.assertFalse(release_delivery(
            1, first["claimed_at"], first["claim_token"], "stale worker"
        ))
        self.assertIsNone(claim_next_delivery())
        with self.connect() as connection:
            token = connection.execute(
                "SELECT claim_token FROM access_deliveries WHERE order_id = 1"
            ).fetchone()[0]
        self.assertEqual(token, second["claim_token"])

    def test_claim_does_not_change_order(self):
        claim_next_delivery()
        with self.connect() as connection:
            row = connection.execute(
                "SELECT id FROM orders WHERE id = 1"
            ).fetchone()
        self.assertEqual(row[0], 1)


if __name__ == "__main__":
    unittest.main()
