"""Durable, fail-closed reservations for course join requests.

Telegram approval is an external side effect. Once a claim is persisted, it
must never be silently retried after an ambiguous result.
"""

from datetime import datetime, timezone

from database import get_connection


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_join_claims_table():
    """Idempotent migration; preserves existing claims and course data."""
    with get_connection() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS course_join_claims (
                invite_link TEXT PRIMARY KEY,
                telegram_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'approving',
                claimed_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                source_type TEXT,
                source_id INTEGER,
                last_error TEXT,
                approved_at TEXT,
                completed_at TEXT
            )
        """)
        columns = {row[1] for row in db.execute('PRAGMA table_info(course_join_claims)')}
        for col, col_type in (
            ('source_type', 'TEXT'), ('source_id', 'INTEGER'),
            ('last_error', 'TEXT'), ('approved_at', 'TEXT'),
            ('completed_at', 'TEXT'),
        ):
            if col not in columns:
                db.execute(f'ALTER TABLE course_join_claims ADD COLUMN {col} {col_type}')
        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_join_claims_review
            ON course_join_claims(status, claimed_at)
        """)


def _expiry_valid(raw: str | None) -> bool:
    if not raw:
        return False
    try:
        parsed = datetime.fromisoformat(raw)
        return (parsed.tzinfo is not None
                and parsed.astimezone(timezone.utc) > datetime.now(timezone.utc))
    except (TypeError, ValueError, OverflowError):
        return False


def reserve_join(invite_link, telegram_id, chat_id, expected_chat_id):
    """Atomically reserve an invitation. Returns reserved/deny/ignore/review.

    Unknown/manual links are intentionally ignored; owners still manage them.
    Ambiguous matching records fail closed instead of guessing an owner.
    """
    if (
        not isinstance(invite_link, str) or not invite_link
        or type(telegram_id) is not int or telegram_id <= 0
        or type(chat_id) is not int
        or type(expected_chat_id) is not int
        or chat_id != expected_chat_id or expected_chat_id == 0
    ):
        return 'ignore'

    with get_connection() as db:
        db.execute('BEGIN IMMEDIATE')
        matches = []
        matches.extend(db.execute("""
            SELECT 'delivery', d.order_id, d.telegram_id, u.telegram_id,
                   o.status, d.status, d.invite_expires_at
            FROM access_deliveries AS d
            JOIN orders AS o ON o.id = d.order_id
            JOIN users AS u ON u.id = o.user_id
            WHERE d.invite_link = ?
        """, (invite_link,)).fetchall())
        recovery_table = db.execute("""
            SELECT 1 FROM sqlite_master
            WHERE type = 'table' AND name = 'access_recoveries'
        """).fetchone()
        if recovery_table:
            matches.extend(db.execute("""
                SELECT 'recovery', r.rowid, r.telegram_id, u.telegram_id,
                       o.status, r.status, r.invite_expires_at
                FROM access_recoveries AS r
                JOIN orders AS o ON o.id = r.order_id
                JOIN users AS u ON u.id = o.user_id
                WHERE r.invite_link = ?
            """, (invite_link,)).fetchall())

        if not matches:
            return 'ignore'
        if len(matches) != 1:
            return 'review'

        kind, reference_id, owner_id, buyer_id, order_status, status, expiry = matches[0]
        eligible_statuses = ('pending', 'delivered') if kind == 'delivery' else ('ready', 'delivered')
        if (
            owner_id != telegram_id or buyer_id != telegram_id
            or order_status != 'paid' or status not in eligible_statuses
            or not _expiry_valid(expiry)
        ):
            return 'deny'

        if db.execute('SELECT 1 FROM course_join_claims WHERE invite_link = ?',
                      (invite_link,)).fetchone():
            return 'review'

        now = _utcnow()
        db.execute("""
            INSERT INTO course_join_claims (
                invite_link, telegram_id, chat_id, status, claimed_at,
                updated_at, source_type, source_id
            ) VALUES (?, ?, ?, 'approving', ?, ?, ?, ?)
        """, (invite_link, telegram_id, chat_id, now, now, kind, reference_id))
        return 'reserved'


def change_join_status(
    invite_link: str,
    telegram_id: int,
    expected_status: str,
    new_status: str,
    *,
    error: str | None = None,
) -> bool:
    """Persist the result with compare-and-swap. No automatic reapproval."""
    transitions = {
        'approving': {'approved', 'needs_review'},
        'approved': {'completed', 'needs_review'},
    }
    if (
        new_status not in transitions.get(expected_status, set())
        or not isinstance(invite_link, str) or not invite_link
        or type(telegram_id) is not int or telegram_id <= 0
    ):
        return False
    now = _utcnow()
    with get_connection() as db:
        cursor = db.execute("""
            UPDATE course_join_claims SET
                status = ?, updated_at = ?,
                last_error = COALESCE(?, last_error),
                approved_at = CASE WHEN ? = 'approved' THEN ? ELSE approved_at END,
                completed_at = CASE WHEN ? = 'completed' THEN ? ELSE completed_at END
            WHERE invite_link = ? AND telegram_id = ? AND status = ?
        """, (
            new_status, now, (error or '')[:500] or None,
            new_status, now, new_status, now,
            invite_link, telegram_id, expected_status,
        ))
        return cursor.rowcount == 1
