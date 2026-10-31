"""Read-only report for manual reconciliation of course join requests.

Usage: python -m integrations.join_review_report --db /path/to/education.db
Does not print invitation URLs, credentials, or modify the database.
"""

import argparse
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path


def get_review_claims(path: str, *, stale_minutes: int = 10) -> list[dict]:
    if not isinstance(stale_minutes, int) or stale_minutes <= 0:
        raise ValueError('stale_minutes must be positive')
    db_path = Path(path).expanduser().resolve(strict=True)
    if not db_path.is_file():
        raise ValueError('Database path is not a file')

    uri = db_path.as_uri() + '?mode=ro'
    with sqlite3.connect(uri, uri=True) as db:
        db.row_factory = sqlite3.Row
        exists = db.execute("""
            SELECT 1 FROM sqlite_master
            WHERE type = 'table' AND name = 'course_join_claims'
        """).fetchone()
        if exists is None:
            return []
        rows = db.execute("""
            SELECT telegram_id, chat_id, status, claimed_at, updated_at
            FROM course_join_claims
            WHERE status IN ('approving', 'approved', 'needs_review')
            ORDER BY claimed_at, telegram_id
        """).fetchall()

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=stale_minutes)
    result = []
    for row in rows:
        state = row['status']
        timestamp = row['updated_at']
        try:
            dt = datetime.fromisoformat(timestamp)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            dt = None
        if state == 'needs_review' or dt is None or dt <= cutoff:
            result.append({
                'telegram_id': row['telegram_id'],
                'chat_id': row['chat_id'],
                'status': state,
                'claimed_at': row['claimed_at'],
                'updated_at': timestamp,
                'reason': 'explicit_review' if state == 'needs_review' else 'stale_or_invalid_timestamp',
            })
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description='Read-only course join review report')
    parser.add_argument('--db', required=True)
    parser.add_argument('--stale-minutes', type=int, default=10)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(get_review_claims(args.db, stale_minutes=args.stale_minutes),
                         ensure_ascii=False, indent=2))
    except (OSError, sqlite3.Error, ValueError) as exc:
        parser.exit(1, f'Cannot inspect join claims: {exc}\n')


if __name__ == '__main__':
    main()
