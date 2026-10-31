import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from integrations.robokassa import make_signature
from integrations.payment_callback import process_payment_notification
from integrations.access_delivery import init_delivery_table


class TestRobokassaPayments(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.db"

        connection = sqlite3.connect(self.db_path)

        connection.executescript("""
            CREATE TABLE users (
                id INTEGER PRIMARY KEY,
                telegram_id INTEGER NOT NULL
            );

            CREATE TABLE orders (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                amount TEXT NOT NULL,
                currency TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                payment_id TEXT
            );

            INSERT INTO users (id, telegram_id)
            VALUES (1, 123456789);

            INSERT INTO orders (
                id, user_id, amount, currency, status
            )
            VALUES (1, 1, '2499', 'RUB', 'pending');
        """)

        connection.commit()
        connection.close()

        def test_connection():
            return sqlite3.connect(self.db_path)

        with patch(
            "integrations.access_delivery.get_connection",
            side_effect=test_connection,
        ):
            init_delivery_table()
        self.connection_patch = patch(
            "integrations.payment_callback.get_connection",
            side_effect=test_connection,
        )
        self.connection_patch.start()

        self.password = "fake_test_password"

    def tearDown(self):
        self.connection_patch.stop()
        self.temp_dir.cleanup()

    def payment(self, amount="2499.00", order_id="1", signature=None):
        if signature is None:
            signature = make_signature([amount, order_id, self.password])

        return process_payment_notification(
            out_sum=amount,
            inv_id=order_id,
            signature=signature,
            password2=self.password,
        )

    def test_valid_payment(self):
        result = self.payment()

        self.assertTrue(result.accepted)
        self.assertTrue(result.newly_paid)
        self.assertEqual(result.telegram_id, 123456789)

    def test_invalid_signature(self):
        result = self.payment(signature="wrong")

        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, "invalid_signature")

    def test_wrong_amount(self):
        result = self.payment(amount="100.00")

        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, "amount_mismatch")

    def test_duplicate_notification(self):
        first = self.payment()
        second = self.payment()

        self.assertTrue(first.newly_paid)
        self.assertTrue(second.accepted)
        self.assertFalse(second.newly_paid)
        self.assertEqual(second.reason, "already_paid")

    def test_unknown_order(self):
        result = self.payment(order_id="999")

        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, "order_not_found")

    def test_payment_creates_access_delivery(self):
        result = self.payment()

        self.assertTrue(result.accepted)
        self.assertTrue(result.newly_paid)

        with sqlite3.connect(self.db_path) as connection:
            order = connection.execute(
                "SELECT status FROM orders WHERE id = 1"
            ).fetchone()

            delivery = connection.execute(
                """
                SELECT telegram_id, status
                FROM access_deliveries
                WHERE order_id = 1
                """
            ).fetchone()

        self.assertEqual(order[0], "paid")
        self.assertEqual(delivery, (123456789, "pending"))

    def test_duplicate_payment_does_not_duplicate_delivery(self):
        first = self.payment()
        second = self.payment()

        self.assertTrue(first.accepted)
        self.assertTrue(second.accepted)
        self.assertFalse(second.newly_paid)

        with sqlite3.connect(self.db_path) as connection:
            count = connection.execute(
                """
                SELECT COUNT(*)
                FROM access_deliveries
                WHERE order_id = 1
                """
            ).fetchone()[0]

        self.assertEqual(count, 1)

    def test_delivery_error_rolls_back_payment(self):
        with sqlite3.connect(self.db_path) as connection:
            connection.execute("DROP TABLE access_deliveries")

        with self.assertRaises(sqlite3.OperationalError):
            self.payment()

        with sqlite3.connect(self.db_path) as connection:
            order = connection.execute(
                """
                SELECT status, payment_id
                FROM orders
                WHERE id = 1
                """
            ).fetchone()

        self.assertEqual(order, ("pending", None))


if __name__ == "__main__":
    unittest.main()
