"""Persistent order -> Telegram checkout message mapping (test bot)."""

import sqlite3
from pathlib import Path

from database import DATABASE_PATH as DB_PATH


def remember_payment_screen(
    order_id: int,
    telegram_id: int,
    message_id: int,
) -> None:
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS payment_screen_messages (
                order_id INTEGER PRIMARY KEY,
                telegram_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO payment_screen_messages (
                order_id, telegram_id, message_id
            )
            VALUES (?, ?, ?)
            ON CONFLICT(order_id) DO UPDATE SET
                telegram_id = excluded.telegram_id,
                message_id = excluded.message_id
            """,
            (order_id, telegram_id, message_id),
        )


def get_payment_screen(order_id: int):
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS payment_screen_messages (
                order_id INTEGER PRIMARY KEY,
                telegram_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL
            )
            """
        )
        return conn.execute(
            """
            SELECT telegram_id, message_id
            FROM payment_screen_messages
            WHERE order_id = ?
            """,
            (order_id,),
        ).fetchone()


def forget_payment_screen(order_id: int) -> None:
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        conn.execute(
            "DELETE FROM payment_screen_messages WHERE order_id = ?",
            (order_id,),
        )
