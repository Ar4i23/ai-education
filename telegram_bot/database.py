"""SQLite database for the AI Education Telegram bot."""

import os
import sqlite3
import uuid
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

DATABASE_PATH = Path(os.getenv("DATABASE_PATH") or (Path(__file__).resolve().parent / "education.db")).expanduser().resolve()


class CourseAlreadyPurchasedError(ValueError):
    """The user has already purchased the course."""


def get_connection():
    connection = sqlite3.connect(DATABASE_PATH, timeout=10)
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_database():
    """Initialize the database without deleting existing records."""
    with get_connection() as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL UNIQUE,
                username TEXT,
                first_name TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)

        connection.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                amount TEXT NOT NULL,
                currency TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                promo_code TEXT,
                payment_id TEXT UNIQUE,
                idempotence_key TEXT UNIQUE,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)

        connection.execute("""
            CREATE TABLE IF NOT EXISTS promo_codes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL UNIQUE,
                discount_amount TEXT NOT NULL,
                source TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                expires_at TEXT,
                max_uses INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)

        connection.execute("""
            CREATE TABLE IF NOT EXISTS legal_acceptances (
                order_id INTEGER PRIMARY KEY,
                telegram_id INTEGER NOT NULL,
                offer_version TEXT NOT NULL,
                privacy_version TEXT NOT NULL,
                accepted_at TEXT NOT NULL,
                FOREIGN KEY (order_id) REFERENCES orders(id)
            )
        """)

        user_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(users)")
        }

        if "active_promo_code" not in user_columns:
            connection.execute("ALTER TABLE users ADD COLUMN active_promo_code TEXT")

        if "first_source" not in user_columns:
            connection.execute("ALTER TABLE users ADD COLUMN first_source TEXT")

        order_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(orders)")
        }

        if "idempotence_key" not in order_columns:
            connection.execute("ALTER TABLE orders ADD COLUMN idempotence_key TEXT")

        if "source" not in order_columns:
            connection.execute("ALTER TABLE orders ADD COLUMN source TEXT")

        connection.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS
            idx_orders_idempotence_key
            ON orders(idempotence_key)
        """)

        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_orders_source
            ON orders(source)
        """)

        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_orders_promo_code
            ON orders(promo_code)
        """)


def save_user(
    telegram_id: int,
    username: str | None,
    first_name: str | None,
):
    """Create or update a user without resetting their promo."""
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO users (telegram_id, username, first_name)
            VALUES (?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                username = excluded.username,
                first_name = excluded.first_name
            """,
            (telegram_id, username, first_name),
        )


def get_user(telegram_id: int):
    with get_connection() as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT * FROM users WHERE telegram_id = ?",
            (telegram_id,),
        ).fetchone()
        return dict(row) if row else None


def get_paid_order(telegram_id: int):
    """Return the latest paid order belonging to a Telegram user."""
    with get_connection() as connection:
        connection.row_factory = sqlite3.Row

        row = connection.execute(
            """
            SELECT
                orders.id,
                orders.user_id,
                users.telegram_id,
                orders.amount,
                orders.currency,
                orders.status,
                orders.promo_code,
                orders.source,
                orders.payment_id,
                orders.idempotence_key,
                orders.created_at
            FROM orders
            JOIN users ON users.id = orders.user_id
            WHERE users.telegram_id = ?
              AND orders.status = 'paid'
            ORDER BY orders.id DESC
            LIMIT 1
            """,
            (telegram_id,),
        ).fetchone()

        return dict(row) if row else None


def save_user_promo(telegram_id: int, code: str) -> bool:
    """Save one valid promo per user."""
    normalized = code.strip().upper()
    promo = get_promo_code(normalized)

    if promo is None:
        return False

    with get_connection() as connection:
        cursor = connection.execute(
            """
            UPDATE users
            SET active_promo_code = ?,
                first_source = COALESCE(first_source, ?)
            WHERE telegram_id = ?
            """,
            (promo["code"], promo["source"], telegram_id),
        )
        return cursor.rowcount == 1


def get_user_promo(telegram_id: int):
    user = get_user(telegram_id)

    if not user or not user["active_promo_code"]:
        return None

    return get_promo_code(user["active_promo_code"])


def clear_user_promo(telegram_id: int):
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE users
            SET active_promo_code = NULL
            WHERE telegram_id = ?
            """,
            (telegram_id,),
        )


def create_order(
    telegram_id: int,
    amount: str,
    currency: str,
    promo_code: str | None = None,
    source: str | None = None,
) -> int:
    """Create a pending order unless the course is already purchased."""
    idempotence_key = str(uuid.uuid4())

    if source is None:
        source = "direct" if promo_code is None else "unknown"

    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")

        user_row = connection.execute(
            "SELECT id FROM users WHERE telegram_id = ?",
            (telegram_id,),
        ).fetchone()

        if user_row is None:
            raise ValueError("Telegram user not found in database")

        user_id = user_row[0]

        already_paid = connection.execute(
            """
            SELECT 1
            FROM orders
            WHERE user_id = ?
              AND status = 'paid'
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()

        if already_paid:
            raise CourseAlreadyPurchasedError("Course already purchased")

        cursor = connection.execute(
            """
            INSERT INTO orders (
                user_id,
                amount,
                currency,
                promo_code,
                source,
                idempotence_key
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                amount,
                currency,
                promo_code,
                source,
                idempotence_key,
            ),
        )

        return cursor.lastrowid


def get_order(order_id: int):
    with get_connection() as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            SELECT
                orders.id,
                orders.user_id,
                users.telegram_id,
                orders.amount,
                orders.currency,
                orders.status,
                orders.promo_code,
                orders.source,
                orders.payment_id,
                orders.idempotence_key,
                orders.created_at
            FROM orders
            JOIN users ON orders.user_id = users.id
            WHERE orders.id = ?
            """,
            (order_id,),
        ).fetchone()
        return dict(row) if row else None


def attach_payment(order_id: int, payment_id: str) -> bool:
    with get_connection() as connection:
        cursor = connection.execute(
            """
            UPDATE orders
            SET payment_id = ?
            WHERE id = ?
              AND status = 'pending'
              AND payment_id IS NULL
            """,
            (payment_id, order_id),
        )
        return cursor.rowcount == 1


def mark_order_paid(order_id: int, payment_id: str) -> bool:
    """Only call after independent payment verification."""
    with get_connection() as connection:
        cursor = connection.execute(
            """
            UPDATE orders
            SET status = 'paid'
            WHERE id = ?
              AND payment_id = ?
              AND status = 'pending'
            """,
            (order_id, payment_id),
        )
        return cursor.rowcount == 1


def get_order_by_payment_id(payment_id: str):
    with get_connection() as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            SELECT
                orders.id,
                orders.user_id,
                users.telegram_id,
                orders.amount,
                orders.currency,
                orders.status,
                orders.promo_code,
                orders.source,
                orders.payment_id,
                orders.idempotence_key,
                orders.created_at
            FROM orders
            JOIN users ON orders.user_id = users.id
            WHERE orders.payment_id = ?
            """,
            (payment_id,),
        ).fetchone()
        return dict(row) if row else None


def create_promo_code(
    code: str,
    discount_amount: str,
    source: str,
    expires_at: str | None = None,
    max_uses: int | None = None,
):
    normalized = code.strip().upper()

    if not normalized or len(normalized) > 32:
        raise ValueError("Promo code must contain 1-32 characters")

    try:
        discount = Decimal(discount_amount)

        if (
            not discount.is_finite()
            or discount <= 0
            or discount != discount.quantize(Decimal("0.01"))
        ):
            raise ValueError("Invalid discount amount")
    except InvalidOperation as exc:
        raise ValueError("Invalid discount amount") from exc

    if not source.strip():
        raise ValueError("Promo source is required")

    if max_uses is not None and max_uses <= 0:
        raise ValueError("max_uses must be positive")

    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO promo_codes (
                code,
                discount_amount,
                source,
                expires_at,
                max_uses
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                normalized,
                str(discount),
                source.strip(),
                expires_at,
                max_uses,
            ),
        )


def get_promo_code(code: str):
    normalized = code.strip().upper()

    with get_connection() as connection:
        connection.row_factory = sqlite3.Row

        row = connection.execute(
            """
            SELECT *
            FROM promo_codes
            WHERE code = ?
              AND is_active = 1
            """,
            (normalized,),
        ).fetchone()

        if row is None:
            return None

        promo = dict(row)

        if promo["expires_at"]:
            try:
                expiry = datetime.fromisoformat(promo["expires_at"])

                if expiry.tzinfo is None:
                    expiry = expiry.replace(tzinfo=UTC)

                if datetime.now(UTC) >= expiry:
                    return None
            except ValueError:
                return None

        if promo["max_uses"] is not None:
            uses = connection.execute(
                """
                SELECT COUNT(*)
                FROM orders
                WHERE promo_code = ?
                  AND status = 'paid'
                """,
                (normalized,),
            ).fetchone()[0]

            if uses >= promo["max_uses"]:
                return None

        return promo


def calculate_discounted_price(
    original_price: str,
    discount_amount: str,
) -> str:
    """Calculate one fixed discount from the original price."""
    try:
        price = Decimal(original_price)
        discount = Decimal(discount_amount)

        if (
            not price.is_finite()
            or not discount.is_finite()
            or price <= 0
            or discount <= 0
            or discount > price
        ):
            raise ValueError("Invalid price or discount")

        result = price - discount
        return format(result.quantize(Decimal("0.01")), "f")

    except InvalidOperation as exc:
        raise ValueError("Invalid price or discount") from exc


def get_sales_statistics():
    """Summarize paid orders by recorded source."""
    with get_connection() as connection:
        connection.row_factory = sqlite3.Row

        rows = connection.execute(
            """
            SELECT
                COALESCE(source, 'unknown') AS source,
                COUNT(*) AS paid_orders,
                SUM(CAST(amount AS REAL)) AS revenue
            FROM orders
            WHERE status = 'paid'
            GROUP BY COALESCE(source, 'unknown')
            ORDER BY paid_orders DESC
            """
        ).fetchall()

        return [dict(row) for row in rows]


def get_latest_pending_order(
    telegram_id: int,
    promo_code: str | None = None,
):
    """Return the latest pending order, optionally filtered by promo."""
    with get_connection() as connection:
        connection.row_factory = sqlite3.Row

        row = connection.execute(
            """
            SELECT
                orders.id,
                orders.user_id,
                users.telegram_id,
                orders.amount,
                orders.currency,
                orders.status,
                orders.promo_code,
                orders.source,
                orders.payment_id,
                orders.idempotence_key,
                orders.created_at
            FROM orders
            JOIN users ON orders.user_id = users.id
            WHERE users.telegram_id = ?
              AND orders.status = 'pending'
              AND (? IS NULL OR orders.promo_code = ?)
            ORDER BY orders.id DESC
            LIMIT 1
            """,
            (telegram_id, promo_code, promo_code),
        ).fetchone()

        return dict(row) if row else None


def get_or_create_pending_order(
    telegram_id: int,
    amount: str,
    currency: str,
    promo_code: str | None = None,
    source: str | None = None,
) -> tuple[int, bool]:
    """
    Return an existing matching pending order or create a new one.

    Returns:
        (order_id, created)
    """
    normalized_promo = promo_code.strip().upper() if promo_code else None
    order_source = source or ("direct" if normalized_promo is None else "unknown")

    # Serialize competing writes within the transaction.
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")

        user_row = connection.execute(
            "SELECT id FROM users WHERE telegram_id = ?",
            (telegram_id,),
        ).fetchone()

        if user_row is None:
            raise ValueError("Telegram user not found in database")

        user_id = user_row[0]

        # Block repeat purchases, including after deleting Telegram chat.
        already_paid = connection.execute(
            """
            SELECT 1
            FROM orders
            WHERE user_id = ?
              AND status = 'paid'
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()

        if already_paid:
            raise CourseAlreadyPurchasedError("Course already purchased")

        existing = connection.execute(
            """
            SELECT id
            FROM orders
            WHERE user_id = ?
              AND status = 'pending'
              AND amount = ?
              AND currency = ?
              AND promo_code IS ?
              AND source = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (
                user_id,
                amount,
                currency,
                normalized_promo,
                order_source,
            ),
        ).fetchone()

        if existing is not None:
            return existing[0], False

        cursor = connection.execute(
            """
            INSERT INTO orders (
                user_id,
                amount,
                currency,
                promo_code,
                source,
                idempotence_key
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                amount,
                currency,
                normalized_promo,
                order_source,
                str(uuid.uuid4()),
            ),
        )

        return cursor.lastrowid, True


def has_legal_acceptance(order_id: int, telegram_id: int) -> bool:
    with get_connection() as connection:
        return connection.execute(
            "SELECT 1 FROM legal_acceptances WHERE order_id=? AND telegram_id=?",
            (order_id, telegram_id),
        ).fetchone() is not None


def record_legal_acceptance(order_id: int, telegram_id: int) -> bool:
    """Accept only the user's pending order; keep first acceptance timestamp."""
    with get_connection() as connection:
        row = connection.execute("""
            SELECT 1 FROM orders o JOIN users u ON u.id=o.user_id
            WHERE o.id=? AND u.telegram_id=? AND o.status='pending'
        """, (order_id, telegram_id)).fetchone()
        if row is None:
            return False
        connection.execute("""
            INSERT OR IGNORE INTO legal_acceptances
            (order_id, telegram_id, offer_version, privacy_version, accepted_at)
            VALUES (?, ?, ?, ?, ?)
        """, (order_id, telegram_id, '2026-10-26', '2026-10-26',
              datetime.now(UTC).isoformat()))
        return True
