import sqlite3
import tempfile
import unittest

from pathlib import Path
from unittest.mock import patch

from integrations.join_claims import (
    init_join_claims_table,
    change_join_status,
)


class JoinStatusTests(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "status.db"

        self.patcher = patch(
            "integrations.join_claims.get_connection",
            side_effect=lambda: sqlite3.connect(self.path),
        )
        self.patcher.start()

        init_join_claims_table()

        with sqlite3.connect(self.path) as db:
            db.execute("""
                INSERT INTO course_join_claims (
                    invite_link, telegram_id, chat_id,
                    status, claimed_at, updated_at
                )
                VALUES (?, ?, ?, 'approving', ?, ?)
            """, (
                "test-link",
                111111,
                -1004307729335,
                "2026-10-30T00:00:00+00:00",
                "2026-10-30T00:00:00+00:00",
            ))

    def tearDown(self):
        self.patcher.stop()
        self.temp.cleanup()

    def status(self):
        with sqlite3.connect(self.path) as db:
            return db.execute(
                "SELECT status FROM course_join_claims"
            ).fetchone()[0]

    def test_approve_and_complete(self):
        self.assertTrue(
            change_join_status(
                "test-link", 111111, "approving", "approved"
            )
        )

        self.assertTrue(
            change_join_status(
                "test-link", 111111, "approved", "completed"
            )
        )

        self.assertEqual(self.status(), "completed")

    def test_wrong_user_cannot_change_status(self):
        self.assertFalse(
            change_join_status(
                "test-link", 222222, "approving", "approved"
            )
        )
        self.assertEqual(self.status(), "approving")

    def test_cannot_skip_approval(self):
        self.assertFalse(
            change_join_status(
                "test-link", 111111, "approving", "completed"
            )
        )
        self.assertEqual(self.status(), "approving")

    def test_review_state_blocks_retry(self):
        self.assertTrue(
            change_join_status(
                "test-link", 111111, "approving", "needs_review"
            )
        )
        self.assertFalse(
            change_join_status(
                "test-link", 111111, "needs_review", "approved"
            )
        )
        self.assertEqual(self.status(), "needs_review")


if __name__ == "__main__":
    unittest.main()
