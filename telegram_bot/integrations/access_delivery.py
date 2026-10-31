"""Persistent queue for Telegram course access delivery."""
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from database import get_connection


def init_delivery_table():
    with get_connection() as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS access_deliveries (
                order_id INTEGER PRIMARY KEY,
                telegram_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                invite_link TEXT,
                invite_expires_at TEXT,
                claimed_at TEXT,
                claim_token TEXT,
                invite_started_at TEXT,
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                delivered_at TEXT,
                FOREIGN KEY (order_id) REFERENCES orders(id)
            )
        """)
        existing = {row[1] for row in connection.execute('PRAGMA table_info(access_deliveries)')}
        for name in ('invite_expires_at', 'claimed_at', 'claim_token', 'invite_started_at'):
            if name not in existing:
                connection.execute(f'ALTER TABLE access_deliveries ADD COLUMN {name} TEXT')


def enqueue_delivery(order_id: int, telegram_id: int) -> bool:
    with get_connection() as connection:
        cursor = connection.execute("""
            INSERT OR IGNORE INTO access_deliveries (order_id, telegram_id)
            SELECT orders.id, users.telegram_id
            FROM orders JOIN users ON users.id = orders.user_id
            WHERE orders.id = ? AND users.telegram_id = ? AND orders.status = 'paid'
        """, (order_id, telegram_id))
        return cursor.rowcount == 1


def get_pending_deliveries(limit: int = 20):
    with get_connection() as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute("""
            SELECT order_id, telegram_id, attempts FROM access_deliveries
            WHERE status = 'pending' ORDER BY created_at, order_id LIMIT ?
        """, (limit,)).fetchall()
        return [dict(row) for row in rows]


def claim_next_delivery():
    with get_connection() as connection:
        connection.row_factory = sqlite3.Row
        connection.execute('BEGIN IMMEDIATE')
        task = connection.execute("""
            SELECT order_id, telegram_id, invite_link, invite_expires_at,
                   invite_started_at, attempts
            FROM access_deliveries
            WHERE status = 'pending' AND claimed_at IS NULL AND claim_token IS NULL
            ORDER BY created_at, order_id LIMIT 1
        """).fetchone()
        if task is None:
            return None
        claimed_at = datetime.now(timezone.utc).isoformat()
        claim_token = secrets.token_hex(16)
        cursor = connection.execute("""
            UPDATE access_deliveries SET claimed_at = ?, claim_token = ?
            WHERE order_id = ? AND status = 'pending'
              AND claimed_at IS NULL AND claim_token IS NULL
        """, (claimed_at, claim_token, task['order_id']))
        if cursor.rowcount != 1:
            return None
        result = dict(task)
        result.update(claimed_at=claimed_at, claim_token=claim_token)
        return result


def begin_invite_creation(order_id: int, claimed_at: str, claim_token: str) -> bool:
    """Persist the point of no automatic retry BEFORE calling Telegram."""
    if not claimed_at or not claim_token:
        return False
    with get_connection() as connection:
        cursor = connection.execute("""
            UPDATE access_deliveries
            SET invite_started_at = ?
            WHERE order_id = ? AND status = 'pending'
              AND claimed_at = ? AND claim_token = ?
              AND invite_link IS NULL AND invite_started_at IS NULL
        """, (datetime.now(timezone.utc).isoformat(), order_id, claimed_at, claim_token))
        return cursor.rowcount == 1


def release_delivery(order_id: int, claimed_at: str, claim_token: str,
                     error: str | None = None) -> bool:
    if not claimed_at or not claim_token:
        return False
    with get_connection() as connection:
        cursor = connection.execute("""
            UPDATE access_deliveries
            SET claimed_at = NULL, claim_token = NULL,
                attempts = attempts + CASE WHEN ? IS NOT NULL THEN 1 ELSE 0 END,
                last_error = ?
            WHERE order_id = ? AND status = 'pending'
              AND claimed_at = ? AND claim_token = ?
              AND invite_started_at IS NULL AND invite_link IS NULL
        """, (error, str(error)[:500] if error else None,
              order_id, claimed_at, claim_token))
        return cursor.rowcount == 1


def recover_stale_deliveries(timeout_minutes: int = 10) -> int:
    """Recover only tasks for which Telegram invite creation never began."""
    if timeout_minutes <= 0:
        raise ValueError('timeout_minutes must be positive')
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=timeout_minutes)).isoformat()
    with get_connection() as connection:
        cursor = connection.execute("""
            UPDATE access_deliveries
            SET claimed_at = NULL, claim_token = NULL,
                last_error = 'Worker claim expired; retry required'
            WHERE status = 'pending' AND claimed_at IS NOT NULL
              AND claimed_at < ? AND invite_link IS NULL
              AND invite_started_at IS NULL
        """, (cutoff,))
        return cursor.rowcount


def save_delivery_invite(order_id: int, claimed_at: str, claim_token: str,
                         invite_link: str, invite_expires_at: str) -> bool:
    if not claimed_at or not claim_token or not invite_link:
        raise ValueError('Claim timestamp, token and invite link are required')
    expires_at = datetime.fromisoformat(invite_expires_at)
    if expires_at.tzinfo is None:
        raise ValueError('Invite expiration must include timezone')
    if expires_at <= datetime.now(timezone.utc):
        raise ValueError('Invite must expire in the future')
    with get_connection() as connection:
        cursor = connection.execute("""
            UPDATE access_deliveries SET invite_link = ?, invite_expires_at = ?
            WHERE order_id = ? AND status = 'pending'
              AND claimed_at = ? AND claim_token = ? AND invite_link IS NULL
        """, (invite_link, invite_expires_at, order_id, claimed_at, claim_token))
        return cursor.rowcount == 1


def mark_delivery_success(order_id: int, claimed_at: str,
                          claim_token: str, invite_link: str) -> bool:
    if not claimed_at or not claim_token or not invite_link:
        return False
    with get_connection() as connection:
        cursor = connection.execute("""
            UPDATE access_deliveries
            SET status = 'delivered', delivered_at = ?, last_error = NULL,
                claimed_at = NULL, claim_token = NULL
            WHERE order_id = ? AND status = 'pending'
              AND claimed_at = ? AND claim_token = ? AND invite_link = ?
        """, (datetime.now(timezone.utc).isoformat(), order_id,
              claimed_at, claim_token, invite_link))
        return cursor.rowcount == 1


def mark_delivery_failure(order_id: int, claimed_at: str,
                          claim_token: str, error: str) -> bool:
    return release_delivery(order_id, claimed_at, claim_token, error)
