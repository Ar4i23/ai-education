"""Explicit, durable preflight marker before an external Telegram API call.

The marker is intentionally conservative: a crash after marking, even before
calling Telegram, requires operator review rather than automatic retry.
"""
from database import get_connection

REVIEW_MARKER = 'manual_review: Telegram invite creation started; outcome may be unknown'


def mark_invite_creation_started(order_id: int, claimed_at: str, claim_token: str) -> bool:
    if not claimed_at or not claim_token:
        return False
    with get_connection() as connection:
        cursor = connection.execute(
            """UPDATE access_deliveries
               SET last_error = ?
               WHERE order_id = ? AND status = 'pending'
                 AND claimed_at = ? AND claim_token = ?
                 AND invite_link IS NULL AND last_error IS NULL""",
            (REVIEW_MARKER, order_id, claimed_at, claim_token),
        )
        return cursor.rowcount == 1
