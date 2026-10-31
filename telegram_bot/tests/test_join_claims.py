import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from integrations.join_claims import init_join_claims_table, reserve_join

CHAT_ID = -1004307729335
BUYER_ID = 111111
STRANGER_ID = 222222
LINK = "https://t.me/+test_paid_invite"


class JoinClaimsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "test.db"

        def connect():
            return sqlite3.connect(self.path, timeout=10)

        self.patcher = patch(
            "integrations.join_claims.get_connection",
            side_effect=connect,
        )
        self.patcher.start()

        future = (
            datetime.now(timezone.utc) + timedelta(hours=12)
        ).isoformat()

        with connect() as db:
            db.executescript("""
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY,
                    telegram_id INTEGER
                );
                CREATE TABLE orders (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER,
                    status TEXT
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
                "INSERT INTO access_deliveries VALUES (?, ?, ?, ?, ?)",
                (1, BUYER_ID, "delivered", LINK, future),
            )
            db.execute(
                "INSERT INTO access_deliveries VALUES (?, ?, ?, ?, ?)",
                (2, STRANGER_ID, "delivered",
                 "https://t.me/+unpaid", future),
            )
            db.execute(
                "INSERT INTO access_recoveries VALUES (?, ?, ?, ?, ?)",
                (1, BUYER_ID, "delivered",
                 "https://t.me/+recovery", future),
            )

        init_join_claims_table()

    def tearDown(self):
        self.patcher.stop()
        self.temp.cleanup()

    def reserve(self, link=LINK, user=BUYER_ID, chat=CHAT_ID):
        return reserve_join(link, user, chat, CHAT_ID)

    def test_buyer_reserves_link(self):
        self.assertEqual(self.reserve(), "reserved")

    def test_same_link_cannot_be_reserved_twice(self):
        self.assertEqual(self.reserve(), "reserved")
        self.assertEqual(self.reserve(), "review")

    def test_stranger_rejected(self):
        self.assertEqual(self.reserve(user=STRANGER_ID), "deny")
        self.assertEqual(self.reserve(), "reserved")

    def test_unpaid_order_rejected(self):
        self.assertEqual(
            self.reserve("https://t.me/+unpaid", STRANGER_ID),
            "deny",
        )

    def test_wrong_chat_ignored(self):
        self.assertEqual(
            self.reserve(chat=-1001234567890),
            "ignore",
        )

    def test_expired_link_rejected(self):
        past = (
            datetime.now(timezone.utc) - timedelta(minutes=1)
        ).isoformat()
        with sqlite3.connect(self.path) as db:
            db.execute(
                "UPDATE access_deliveries SET invite_expires_at = ?",
                (past,),
            )
        self.assertEqual(self.reserve(), "deny")

    def test_recovery_reservation(self):
        self.assertEqual(
            self.reserve("https://t.me/+recovery"),
            "reserved",
        )

    def test_concurrent_requests(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.reserve(), range(2)))

        self.assertCountEqual(results, ["reserved", "review"])


    def test_fast_join_while_delivery_pending(self):
        with sqlite3.connect(self.path) as db:
            db.execute(
                "UPDATE access_deliveries SET status = 'pending' "
                "WHERE invite_link = ?",
                (LINK,),
            )

        self.assertEqual(self.reserve(), "reserved")

    def test_fast_join_while_recovery_ready(self):
        with sqlite3.connect(self.path) as db:
            db.execute(
                "UPDATE access_recoveries SET status = 'ready' "
                "WHERE invite_link = ?",
                ("https://t.me/+recovery",),
            )

        self.assertEqual(
            self.reserve("https://t.me/+recovery"),
            "reserved",
        )

    def test_unsaved_invite_cannot_be_used(self):
        with sqlite3.connect(self.path) as db:
            db.execute(
                "UPDATE access_deliveries SET invite_link = NULL "
                "WHERE invite_link = ?",
                (LINK,),
            )

        self.assertEqual(self.reserve(), "ignore")


if __name__ == "__main__":
    unittest.main()
