"""Read-only diagnostics for Telegram course-access deliveries.

Run as: python -m integrations.delivery_diagnostics --db PATH --all
No database migration, payment operation, or Telegram API request occurs.
"""

import argparse
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _parse_time(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def classify_delivery(row, *, now=None, stale_minutes=10):
    """Classify a delivery conservatively without changing it."""
    now = now or datetime.now(timezone.utc)
    status = row['status']
    if status == 'delivered':
        return 'delivered'
    if status != 'pending':
        return 'review_unknown_status'
    claim = row['claimed_at']
    token = row['claim_token']
    link = row['invite_link']
    if link:
        return 'review_saved_invite'
    if bool(claim) != bool(token):
        return 'review_inconsistent_claim'
    if claim:
        when = _parse_time(claim)
        if when is None:
            return 'review_invalid_claim_time'
        if when <= now - timedelta(minutes=stale_minutes):
            return 'review_stale_claim'
        return 'processing'
    return 'ready'


def inspect_deliveries(db_path, *, limit=100, only_review=True, stale_minutes=10):
    """Return diagnostic rows. Database is opened in SQLite read-only mode."""
    if not isinstance(limit, int) or not 1 <= limit <= 10000:
        raise ValueError('limit must be an integer between 1 and 10000')
    if not isinstance(stale_minutes, int) or stale_minutes <= 0:
        raise ValueError('stale_minutes must be a positive integer')
    path = Path(db_path).expanduser().resolve(strict=True)
    if not path.is_file():
        raise ValueError('Database path must be a file')
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute('''
            SELECT order_id, telegram_id, status, attempts, last_error,
                   claimed_at, claim_token, invite_link, invite_expires_at,
                   created_at, delivered_at
            FROM access_deliveries
            ORDER BY created_at DESC, order_id DESC
            LIMIT ?
        ''', (limit,)).fetchall()
    results = []
    for row in rows:
        item = dict(row)
        category = classify_delivery(item, stale_minutes=stale_minutes)
        result = {
            'order_id': item['order_id'],
            'telegram_id': item['telegram_id'],
            'status': item['status'],
            'diagnosis': category,
            'attempts': item['attempts'],
            'has_invite': bool(item['invite_link']),
            'invite_expires_at': item['invite_expires_at'],
            'claimed_at': item['claimed_at'],
            'last_error': item['last_error'],
        }
        if not only_review or category.startswith('review_'):
            results.append(result)
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description='Read-only delivery diagnostics')
    parser.add_argument('--db', required=True, help='SQLite database file')
    parser.add_argument('--all', action='store_true', help='Show all delivery tasks')
    parser.add_argument('--limit', type=int, default=100)
    args = parser.parse_args(argv)
    try:
        results = inspect_deliveries(args.db, limit=args.limit, only_review=not args.all)
    except (OSError, sqlite3.Error, ValueError) as exc:
        parser.exit(1, f'Diagnostic error: {exc}\n')
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
