"""Persistent and conservative recovery of paid course access."""

import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

from database import get_connection

RECOVERY_COOLDOWN = timedelta(hours=24)


def _now():
    return datetime.now(timezone.utc)


def init_recovery_table():
    with get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS access_recoveries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                order_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'reserved',
                token TEXT NOT NULL UNIQUE,
                invite_link TEXT,
                invite_expires_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_error TEXT,
                FOREIGN KEY (order_id) REFERENCES orders(id)
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_recoveries_user
            ON access_recoveries (telegram_id, created_at)
            """
        )


def reserve_recovery(telegram_id: int):
    """Reserve one recovery atomically.

    Returns (status, recovery_id, token).
    """
    if (
        not isinstance(telegram_id, int)
        or isinstance(telegram_id, bool)
        or telegram_id <= 0
    ):
        return "invalid_user", None, None

    now = _now()
    cutoff = (now - RECOVERY_COOLDOWN).isoformat()

    with get_connection() as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN IMMEDIATE")

        paid_order = connection.execute(
            """
            SELECT orders.id
            FROM orders
            JOIN users ON users.id = orders.user_id
            WHERE users.telegram_id = ?
              AND orders.status = 'paid'
            ORDER BY orders.id ASC
            LIMIT 1
            """,
            (telegram_id,),
        ).fetchone()

        if paid_order is None:
            return "not_paid", None, None

        latest = connection.execute(
            """
            SELECT status
            FROM access_recoveries
            WHERE telegram_id = ?
              AND created_at > ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (telegram_id, cutoff),
        ).fetchone()

        if latest is not None:
            return "cooldown", None, None

        token = secrets.token_hex(24)
        timestamp = now.isoformat()

        cursor = connection.execute(
            """
            INSERT INTO access_recoveries (
                telegram_id,
                order_id,
                status,
                token,
                created_at,
                updated_at
            )
            VALUES (?, ?, 'reserved', ?, ?, ?)
            """,
            (
                telegram_id,
                paid_order["id"],
                token,
                timestamp,
                timestamp,
            ),
        )

        return "reserved", cursor.lastrowid, token


def begin_recovery_invite(recovery_id: int, token: str) -> bool:
    """Durable barrier before calling Telegram."""
    with get_connection() as connection:
        cursor = connection.execute(
            """
            UPDATE access_recoveries
            SET status = 'creating',
                updated_at = ?
            WHERE id = ?
              AND token = ?
              AND status = 'reserved'
            """,
            (_now().isoformat(), recovery_id, token),
        )
        return cursor.rowcount == 1


def complete_recovery(
    recovery_id: int,
    token: str,
    invite_link: str,
    expires_at: datetime,
) -> bool:
    if not invite_link or expires_at.tzinfo is None:
        return False

    if expires_at <= _now():
        return False

    with get_connection() as connection:
        cursor = connection.execute(
            """
            UPDATE access_recoveries
            SET status = 'ready',
                invite_link = ?,
                invite_expires_at = ?,
                updated_at = ?
            WHERE id = ?
              AND token = ?
              AND status = 'creating'
              AND invite_link IS NULL
            """,
            (
                invite_link,
                expires_at.isoformat(),
                _now().isoformat(),
                recovery_id,
                token,
            ),
        )
        return cursor.rowcount == 1


def mark_recovery_delivered(recovery_id: int, token: str) -> bool:
    with get_connection() as connection:
        cursor = connection.execute(
            """
            UPDATE access_recoveries
            SET status = 'delivered',
                updated_at = ?
            WHERE id = ?
              AND token = ?
              AND status = 'ready'
            """,
            (_now().isoformat(), recovery_id, token),
        )
        return cursor.rowcount == 1


def mark_recovery_review(
    recovery_id: int,
    token: str,
    error: str,
) -> bool:
    """Flag uncertain outcomes; never retry automatically."""
    with get_connection() as connection:
        cursor = connection.execute(
            """
            UPDATE access_recoveries
            SET status = 'needs_review',
                last_error = ?,
                updated_at = ?
            WHERE id = ?
              AND token = ?
              AND status IN ('reserved', 'creating', 'ready')
            """,
            (
                str(error)[:500],
                _now().isoformat(),
                recovery_id,
                token,
            ),
        )
        return cursor.rowcount == 1
