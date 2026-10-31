"""End-to-end Robokassa -> SQLite -> Telegram delivery (all local mocks)."""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from integrations.access_delivery import init_delivery_table
from integrations.delivery_worker import process_one_delivery
from integrations.payment_callback import process_payment_notification
from integrations.robokassa import make_signature


class PaymentDeliveryFlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "flow.db"
        self.password = "local_dummy_password_not_a_merchant_secret"

        def connect():
            connection = sqlite3.connect(self.db_path, timeout=10)
            connection.execute("PRAGMA foreign_keys = ON")
            return connection

        self.connect = connect
        with connect() as connection:
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
                INSERT INTO users VALUES (1, 123456789);
                INSERT INTO orders (id, user_id, amount, currency, status)
                VALUES (1, 1, '2499', 'RUB', 'pending');
            """)

        self.db_patches = [
            patch("integrations.access_delivery.get_connection", side_effect=connect),
            patch("integrations.payment_callback.get_connection", side_effect=connect),
        ]
        for db_patch in self.db_patches:
            db_patch.start()
        init_delivery_table()

        self.bot = SimpleNamespace(
            create_chat_invite_link=AsyncMock(
                return_value=SimpleNamespace(invite_link="https://t.me/+fake_test_invite")
            ),
            send_message=AsyncMock(),
        )

    def tearDown(self):
        for db_patch in reversed(self.db_patches):
            db_patch.stop()
        self.temp_dir.cleanup()

    def notify(self, amount="2499.00"):
        signature = make_signature([amount, "1", self.password])
        return process_payment_notification(
            out_sum=amount,
            inv_id="1",
            signature=signature,
            password2=self.password,
        )

    def read_state(self):
        with self.connect() as connection:
            order = connection.execute(
                "SELECT status, payment_id FROM orders WHERE id = 1"
            ).fetchone()
            delivery = connection.execute(
                "SELECT status, telegram_id, invite_link, invite_expires_at "
                "FROM access_deliveries WHERE order_id = 1"
            ).fetchone()
        return order, delivery

    async def test_paid_order_delivered_end_to_end(self):
        result = self.notify()
        self.assertTrue(result.accepted)
        self.assertTrue(result.newly_paid)
        self.assertEqual(result.telegram_id, 123456789)

        self.assertEqual(await process_one_delivery(self.bot, -1004307729335), "delivered")
        order, delivery = self.read_state()
        self.assertEqual(order, ("paid", "robokassa:1"))
        self.assertEqual(delivery[:3], (
            "delivered", 123456789, "https://t.me/+fake_test_invite"
        ))
        self.assertIsNotNone(delivery[3])
        self.bot.create_chat_invite_link.assert_awaited_once()
        self.bot.send_message.assert_awaited_once()
        self.assertEqual(self.bot.send_message.await_args.kwargs["chat_id"], 123456789)
        self.assertEqual(
            self.bot.create_chat_invite_link.await_args.kwargs["expire_date"].isoformat(),
            delivery[3],
        )

    async def test_wrong_amount_never_enqueues_or_sends(self):
        result = self.notify(amount="10.00")
        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, "amount_mismatch")
        self.assertEqual(await process_one_delivery(self.bot, -1004307729335), "empty")
        order, delivery = self.read_state()
        self.assertEqual(order, ("pending", None))
        self.assertIsNone(delivery)
        self.bot.create_chat_invite_link.assert_not_awaited()
        self.bot.send_message.assert_not_awaited()

    async def test_duplicate_callback_does_not_resend(self):
        self.assertTrue(self.notify().newly_paid)
        self.assertEqual(await process_one_delivery(self.bot, -1004307729335), "delivered")
        second = self.notify()
        self.assertTrue(second.accepted)
        self.assertFalse(second.newly_paid)
        self.assertEqual(second.reason, "already_paid")
        self.assertEqual(await process_one_delivery(self.bot, -1004307729335), "empty")
        self.bot.create_chat_invite_link.assert_awaited_once()
        self.bot.send_message.assert_awaited_once()
        with self.connect() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM access_deliveries WHERE order_id = 1"
            ).fetchone()[0]
        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
