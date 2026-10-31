"""Read-only eligibility helper for course invitations.

The actual approval path MUST call join_claims.reserve_join(), which also
atomically reserves a link. Never approve based on this helper alone.
"""

from integrations.join_claims import _expiry_valid
from database import get_connection


def is_authorized_join(invite_link: str, telegram_id: int) -> bool:
    """Check paid ownership without reserving or approving the request."""
    if (not isinstance(invite_link, str) or not invite_link
            or type(telegram_id) is not int or telegram_id <= 0):
        return False
    with get_connection() as db:
        records = []
        records.extend(db.execute("""
            SELECT d.telegram_id, u.telegram_id, o.status, d.status,
                   d.invite_expires_at, 'delivery'
            FROM access_deliveries d
            JOIN orders o ON o.id = d.order_id
            JOIN users u ON u.id = o.user_id
            WHERE d.invite_link = ?
        """, (invite_link,)).fetchall())
        if db.execute("""SELECT 1 FROM sqlite_master
                         WHERE type = 'table' AND name = 'access_recoveries'""").fetchone():
            records.extend(db.execute("""
                SELECT r.telegram_id, u.telegram_id, o.status, r.status,
                       r.invite_expires_at, 'recovery'
                FROM access_recoveries r
                JOIN orders o ON o.id = r.order_id
                JOIN users u ON u.id = o.user_id
                WHERE r.invite_link = ?
            """, (invite_link,)).fetchall())
    if len(records) != 1:
        return False
    owner, buyer, paid_status, delivery_status, expiration, kind = records[0]
    valid_states = ('pending', 'delivered') if kind == 'delivery' else ('ready', 'delivered')
    return (owner == buyer == telegram_id
            and paid_status == 'paid'
            and delivery_status in valid_states
            and _expiry_valid(expiration))
