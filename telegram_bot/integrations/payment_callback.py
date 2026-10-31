"""Verification and atomic recording of Robokassa notifications."""

import sqlite3
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from database import get_connection
from integrations.robokassa import verify_result_signature


@dataclass(frozen=True)
class PaymentResult:
    accepted: bool
    order_id: int | None
    telegram_id: int | None
    newly_paid: bool
    reason: str


def process_payment_notification(
    *,
    out_sum: str,
    inv_id: str,
    signature: str,
    password2: str,
    algorithm: str = "sha256",
    invoice_offset: int = 0,
) -> PaymentResult:
    """Verify and record a payment atomically.

    A duplicate purchase is recorded but does not generate
    another course-access delivery.
    """

    try:
        external_id = int(inv_id)
        order_id = external_id - invoice_offset

        if order_id <= 0 or str(external_id) != str(inv_id):
            raise ValueError

        received_amount = Decimal(str(out_sum))

        if (
            not received_amount.is_finite()
            or received_amount <= 0
            or received_amount != received_amount.quantize(Decimal("0.01"))
        ):
            raise ValueError

    except (ValueError, TypeError, InvalidOperation):
        return PaymentResult(False, None, None, False, "invalid_parameters")

    if not verify_result_signature(
        amount=out_sum,
        order_id=inv_id,
        received_signature=signature,
        password2=password2,
        algorithm=algorithm,
    ):
        return PaymentResult(False, order_id, None, False, "invalid_signature")

    payment_id = f"robokassa:{inv_id}"

    with get_connection() as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN IMMEDIATE")

        order = connection.execute(
            """
            SELECT
                orders.id,
                orders.user_id,
                orders.amount,
                orders.currency,
                orders.status,
                orders.payment_id,
                users.telegram_id
            FROM orders
            JOIN users ON users.id = orders.user_id
            WHERE orders.id = ?
            """,
            (order_id,),
        ).fetchone()

        if order is None:
            return PaymentResult(False, order_id, None, False, "order_not_found")

        if order["currency"].upper() != "RUB":
            return PaymentResult(False, order_id, None, False, "invalid_currency")

        try:
            expected_amount = Decimal(str(order["amount"]))
        except (InvalidOperation, ValueError, TypeError):
            return PaymentResult(False, order_id, None, False, "invalid_order_amount")

        if received_amount != expected_amount:
            return PaymentResult(False, order_id, None, False, "amount_mismatch")

        telegram_id = order["telegram_id"]
        user_id = order["user_id"]

        # Repeated notification for the same order.
        if order["status"] == "paid":
            if order["payment_id"] != payment_id:
                return PaymentResult(False, order_id, None, False, "payment_conflict")

            return PaymentResult(True, order_id, telegram_id, False, "already_paid")

        if order["status"] != "pending":
            return PaymentResult(False, order_id, None, False, "invalid_status")

        if order["payment_id"] not in (None, payment_id):
            return PaymentResult(False, order_id, None, False, "payment_conflict")

        # Check whether this user has already purchased the course
        # through another order.
        previous_purchase = connection.execute(
            """
            SELECT id
            FROM orders
            WHERE user_id = ?
              AND status = 'paid'
              AND id != ?
            ORDER BY id ASC
            LIMIT 1
            """,
            (user_id, order_id),
        ).fetchone()

        # Record the confirmed payment even if it is a duplicate.
        cursor = connection.execute(
            """
            UPDATE orders
            SET payment_id = ?, status = 'paid'
            WHERE id = ?
              AND status = 'pending'
              AND (payment_id IS NULL OR payment_id = ?)
            """,
            (payment_id, order_id, payment_id),
        )

        if cursor.rowcount != 1:
            raise RuntimeError("Order payment update failed")

        if previous_purchase is not None:
            # Payment was received, but course access already exists.
            # Do not enqueue a second invitation.
            return PaymentResult(
                True,
                order_id,
                telegram_id,
                False,
                "duplicate_purchase",
            )

        # First successful purchase: enqueue access delivery.
        connection.execute(
            """
            INSERT OR IGNORE INTO access_deliveries (
                order_id, telegram_id
            )
            VALUES (?, ?)
            """,
            (order_id, telegram_id),
        )

        return PaymentResult(True, order_id, telegram_id, True, "paid")
