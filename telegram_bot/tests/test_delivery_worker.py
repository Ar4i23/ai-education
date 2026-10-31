import sqlite3
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from integrations.access_delivery import init_delivery_table
from integrations.delivery_worker import process_one_delivery


class DeliveryWorkerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "worker.db"

        def connect():
            conn = sqlite3.connect(self.db)
            conn.execute("PRAGMA foreign_keys = ON")
            return conn

        self.connect = connect
        self.patcher = patch(
            "integrations.access_delivery.get_connection",
            side_effect=connect,
        )
        self.patcher.start()

        with connect() as conn:
            conn.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY)")
            conn.execute("INSERT INTO orders VALUES (1)")
        init_delivery_table()

    def tearDown(self):
        self.patcher.stop()
        self.tmp.cleanup()

    def queue(self, invite=None):
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO access_deliveries "
                "(order_id, telegram_id, invite_link) VALUES (1, 123, ?)",
                (invite,),
            )

    def row(self):
        with self.connect() as conn:
            return conn.execute(
                "SELECT status, invite_link, claim_token "
                "FROM access_deliveries WHERE order_id = 1"
            ).fetchone()

    async def test_empty_queue(self):
        result = await process_one_delivery(object(), -100123)
        self.assertEqual(result, "empty")

    async def test_successful_delivery(self):
        self.queue()
        with patch(
            "integrations.delivery_worker.create_course_invite_with_expiry",
            new_callable=AsyncMock,
            return_value=("https://t.me/+sample", datetime.now(timezone.utc) + timedelta(hours=24)),
        ) as create, patch(
            "integrations.delivery_worker.send_course_invite",
            new_callable=AsyncMock,
        ) as send:
            result = await process_one_delivery(object(), -100123)

        self.assertEqual(result, "delivered")
        self.assertEqual(self.row()[:2], ("delivered", "https://t.me/+sample"))
        create.assert_awaited_once()
        send.assert_awaited_once()

    async def test_send_failure_is_not_retried(self):
        self.queue()
        with patch(
            "integrations.delivery_worker.create_course_invite_with_expiry",
            new_callable=AsyncMock,
            return_value=("https://t.me/+sample", datetime.now(timezone.utc) + timedelta(hours=24)),
        ), patch(
            "integrations.delivery_worker.send_course_invite",
            new_callable=AsyncMock,
            side_effect=RuntimeError("connection lost"),
        ) as send:
            self.assertEqual(
                await process_one_delivery(object(), -100123), "needs_review"
            )
            self.assertEqual(
                await process_one_delivery(object(), -100123), "empty"
            )
        self.assertEqual(send.await_count, 1)
        self.assertEqual(self.row()[0], "pending")
        self.assertEqual(self.row()[1], "https://t.me/+sample")

    async def test_existing_invite_is_not_resent(self):
        self.queue(invite="https://t.me/+old")
        with patch(
            "integrations.delivery_worker.send_course_invite",
            new_callable=AsyncMock,
        ) as send:
            result = await process_one_delivery(object(), -100123)
        self.assertEqual(result, "needs_review")
        send.assert_not_awaited()

    async def test_create_failure_requires_review(self):
        self.queue()
        with patch(
            "integrations.delivery_worker.create_course_invite_with_expiry",
            new_callable=AsyncMock,
            side_effect=RuntimeError("connection lost"),
        ):
            result = await process_one_delivery(object(), -100123)
        self.assertEqual(result, "needs_review")
        self.assertIsNotNone(self.row()[2])

    async def test_invalid_channel_rejected_without_claim(self):
        self.queue()
        with self.assertRaises(ValueError):
            await process_one_delivery(object(), 0)
        self.assertIsNone(self.row()[2])


if __name__ == "__main__":
    unittest.main()
