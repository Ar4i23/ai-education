import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from integrations.access_delivery import (
    init_delivery_table,
    claim_next_delivery,
    save_delivery_invite,
)


class DeliveryInviteTests(unittest.TestCase):
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
            connection.execute("INSERT INTO orders(id) VALUES (1)")

        init_delivery_table()
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO access_deliveries (order_id, telegram_id) "
                "VALUES (1, 123456789)"
            )

        self.task = claim_next_delivery()
        self.expires_at = (
            datetime.now(timezone.utc) + timedelta(hours=24)
        ).isoformat()

    def tearDown(self):
        self.patcher.stop()
        self.temp_dir.cleanup()

    def save(self, claim=None, token=None, link="https://t.me/+test123"):
        return save_delivery_invite(
            order_id=1,
            claimed_at=claim if claim is not None else self.task["claimed_at"],
            claim_token=token if token is not None else self.task["claim_token"],
            invite_link=link,
            invite_expires_at=self.expires_at,
        )

    def test_saves_invite(self):
        self.assertTrue(self.save())
        with self.connect() as connection:
            row = connection.execute(
                "SELECT invite_link, invite_expires_at FROM access_deliveries "
                "WHERE order_id = 1"
            ).fetchone()
        self.assertEqual(row, ("https://t.me/+test123", self.expires_at))

    def test_cannot_overwrite_invite(self):
        self.assertTrue(self.save())
        self.assertFalse(self.save(link="https://t.me/+another"))

    def test_rejects_wrong_claim(self):
        self.assertFalse(self.save(claim="2020-01-01T00:00:00+00:00"))

    def test_rejects_wrong_token(self):
        self.assertFalse(self.save(token="wrong-token"))
        with self.connect() as connection:
            link = connection.execute(
                "SELECT invite_link FROM access_deliveries WHERE order_id = 1"
            ).fetchone()[0]
        self.assertIsNone(link)

    def test_rejects_expired_invite(self):
        expired = (
            datetime.now(timezone.utc) - timedelta(minutes=1)
        ).isoformat()
        with self.assertRaises(ValueError):
            save_delivery_invite(
                order_id=1,
                claimed_at=self.task["claimed_at"],
                claim_token=self.task["claim_token"],
                invite_link="https://t.me/+expired",
                invite_expires_at=expired,
            )


if __name__ == "__main__":
    unittest.main()
