"""Tests for paid Telegram course join authorization."""

import sqlite3
import tempfile
import unittest

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from integrations.join_authorization import is_authorized_join


class JoinAuthorizationTests(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.db"

        self.future = (
            datetime.now(timezone.utc) + timedelta(hours=12)
        ).isoformat()

        self.past = (
            datetime.now(timezone.utc) - timedelta(hours=1)
        ).isoformat()

        with sqlite3.connect(self.db_path) as db:
            db.executescript("""
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY,
                    telegram_id INTEGER NOT NULL
                );

                CREATE TABLE orders (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    status TEXT NOT NULL
                );

                CREATE TABLE access_deliveries (
                    order_id INTEGER,
                    telegram_id INTEGER,
                    status TEXT,
                    invite_link TEXT,
                    invite_expires_at TEXT
                );

                CREATE TABLE access_recoveries (
                    order_id INTEGER,
                    telegram_id INTEGER,
                    status TEXT,
                    invite_link TEXT,
                    invite_expires_at TEXT
                );

                INSERT INTO users VALUES (1, 111111);
                INSERT INTO users VALUES (2, 222222);

                INSERT INTO orders VALUES (1, 1, 'paid');
                INSERT INTO orders VALUES (2, 2, 'pending');
            """)

            db.execute(
                """
                INSERT INTO access_deliveries
                VALUES (1, 111111, 'delivered', ?, ?)
                """,
                ("https://t.me/+paid_test", self.future),
            )

            db.execute(
                """
                INSERT INTO access_deliveries
                VALUES (2, 222222, 'delivered', ?, ?)
                """,
                ("https://t.me/+unpaid_test", self.future),
            )

            db.execute(
                """
                INSERT INTO access_recoveries
                VALUES (1, 111111, 'delivered', ?, ?)
                """,
                ("https://t.me/+recovery_test", self.future),
            )

        self.db_patch = patch(
            "integrations.join_authorization.get_connection",
            side_effect=lambda: sqlite3.connect(self.db_path),
        )
        self.db_patch.start()

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_paid_buyer_allowed(self):
        self.assertTrue(
            is_authorized_join("https://t.me/+paid_test", 111111)
        )

    def test_other_user_rejected(self):
        self.assertFalse(
            is_authorized_join("https://t.me/+paid_test", 222222)
        )

    def test_unpaid_order_rejected(self):
        self.assertFalse(
            is_authorized_join("https://t.me/+unpaid_test", 222222)
        )

    def test_unknown_link_rejected(self):
        self.assertFalse(
            is_authorized_join("https://t.me/+unknown", 111111)
        )

    def test_expired_link_rejected(self):
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                """
                UPDATE access_deliveries
                SET invite_expires_at = ?
                WHERE order_id = 1
                """,
                (self.past,),
            )

        self.assertFalse(
            is_authorized_join("https://t.me/+paid_test", 111111)
        )

    def test_recovery_buyer_allowed(self):
        self.assertTrue(
            is_authorized_join("https://t.me/+recovery_test", 111111)
        )

    def test_recovery_other_user_rejected(self):
        self.assertFalse(
            is_authorized_join("https://t.me/+recovery_test", 222222)
        )

    def test_recovery_not_delivered_rejected(self):
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                """
                UPDATE access_recoveries
                SET status = 'reserved'
                """
            )

        self.assertFalse(
            is_authorized_join("https://t.me/+recovery_test", 111111)
        )

    def test_missing_recovery_table_rejected_safely(self):
        with sqlite3.connect(self.db_path) as db:
            db.execute("DROP TABLE access_recoveries")

        self.assertFalse(
            is_authorized_join("https://t.me/+unknown", 111111)
        )


if __name__ == "__main__":
    unittest.main()
