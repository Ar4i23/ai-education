"""Tests for duplicate course purchases through Robokassa."""

import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from integrations.payment_callback import process_payment_notification


class DuplicatePurchaseTests(unittest.TestCase):
    def setUp(self):
        # Every test uses its own temporary SQLite database.
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

        self.db_path = Path(self.temp_dir.name) / "test.db"

        connection = sqlite3.connect(self.db_path)

        connection.executescript(
            """
            CREATE TABLE users (
                id INTEGER PRIMARY KEY,
                telegram_id INTEGER NOT NULL UNIQUE
            );

            CREATE TABLE orders (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                amount TEXT NOT NULL,
                currency TEXT NOT NULL,
                status TEXT NOT NULL,
                payment_id TEXT
            );

            CREATE TABLE access_deliveries (
                order_id INTEGER PRIMARY KEY,
                telegram_id INTEGER NOT NULL
            );

            INSERT INTO users (id, telegram_id)
            VALUES (1, 123456789);

            INSERT INTO orders (
                id, user_id, amount, currency, status, payment_id
            )
            VALUES
                (2, 1, '2499.00', 'RUB', 'paid', 'robokassa:2'),
                (3, 1, '2499.00', 'RUB', 'pending', NULL);

            INSERT INTO access_deliveries (order_id, telegram_id)
            VALUES (2, 123456789);
            """
        )

        connection.commit()
        connection.close()

        def temporary_connection():
            return sqlite3.connect(self.db_path)

        patcher = patch(
            "integrations.payment_callback.get_connection",
            temporary_connection,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

        self.password2 = "local-test-password"

    def notify_order_3(self):
        # Simulate a valid SHA256 Robokassa notification.
        amount = "2499"
        order_id = "3"

        payload = f"{amount}:{order_id}:{self.password2}"
        signature = hashlib.sha256(payload.encode("utf-8")).hexdigest()

        return process_payment_notification(
            out_sum=amount,
            inv_id=order_id,
            signature=signature,
            password2=self.password2,
            algorithm="sha256",
        )

    def test_duplicate_purchase_is_recorded_without_new_access(self):
        result = self.notify_order_3()

        self.assertTrue(result.accepted)
        self.assertEqual(result.order_id, 3)
        self.assertFalse(result.newly_paid)
        self.assertEqual(result.reason, "duplicate_purchase")

        with sqlite3.connect(self.db_path) as connection:
            order = connection.execute(
                "SELECT status, payment_id FROM orders WHERE id = 3"
            ).fetchone()

            deliveries = connection.execute(
                "SELECT order_id FROM access_deliveries ORDER BY order_id"
            ).fetchall()

        # Payment must be recorded, not silently discarded.
        self.assertEqual(order, ("paid", "robokassa:3"))

        # The second payment must not generate another invitation.
        self.assertEqual(deliveries, [(2,)])

    def test_repeated_notification_is_idempotent(self):
        first = self.notify_order_3()
        second = self.notify_order_3()

        self.assertEqual(first.reason, "duplicate_purchase")
        self.assertTrue(second.accepted)
        self.assertFalse(second.newly_paid)
        self.assertEqual(second.reason, "already_paid")

        with sqlite3.connect(self.db_path) as connection:
            deliveries = connection.execute(
                "SELECT COUNT(*) FROM access_deliveries"
            ).fetchone()[0]

        self.assertEqual(deliveries, 1)


if __name__ == "__main__":
    unittest.main()
