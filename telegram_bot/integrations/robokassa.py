import hashlib
import hmac
import json
from decimal import Decimal, InvalidOperation
from urllib.parse import quote, urlencode


PAYMENT_URL = "https://auth.robokassa.ru/Merchant/Index.aspx"


def normalize_amount(value):
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError("Некорректная сумма платежа")

    if not amount.is_finite() or amount <= 0:
        raise ValueError("Сумма должна быть положительной")

    if amount.as_tuple().exponent < -2:
        raise ValueError("Допускается не более двух знаков после запятой")

    return f"{amount:.2f}"


def make_signature(parts, algorithm="sha256"):
    payload = ":".join(str(part) for part in parts)

    if algorithm.lower() not in ("md5", "sha256"):
        raise ValueError("Неподдерживаемый алгоритм подписи")

    return hashlib.new(
        algorithm.lower(),
        payload.encode("utf-8"),
    ).hexdigest()


def build_receipt(amount, item_name, tax="none"):
    amount = normalize_amount(amount)

    if not isinstance(item_name, str) or not item_name.strip():
        raise ValueError("Не указано название услуги")

    receipt = {
        "items": [
            {
                "name": item_name.strip(),
                "quantity": 1,
                "sum": float(Decimal(amount)),
                "cost": float(Decimal(amount)),
                "payment_method": "full_payment",
                "payment_object": "service",
                "tax": tax,
            }
        ]
    }

    return json.dumps(
        receipt,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def build_payment_fields(
    merchant_login, password1, order_id, amount,
    description="Курс ИИ-мультики с нуля", is_test=True,
    algorithm="sha256", include_receipt=False, receipt_tax="none",
):
    """Generate the exact form fields signed by Robokassa.

    Receipt is URL-encoded once before signing; POST submits that encoded
    value as the form field, as described in the merchant API docs.
    """
    amount = normalize_amount(amount)
    signature_parts = [merchant_login, amount, order_id]
    encoded_receipt = None
    if include_receipt:
        receipt = build_receipt(amount, description, tax=receipt_tax)
        encoded_receipt = quote(receipt, safe="")
        signature_parts.append(encoded_receipt)
    signature_parts.append(password1)
    params = {
        "MerchantLogin": merchant_login,
        "OutSum": amount,
        "InvId": str(order_id),
        "Description": description,
        "SignatureValue": make_signature(signature_parts, algorithm),
        "IsTest": "1" if is_test else "0",
        "Culture": "ru",
    }
    if encoded_receipt is not None:
        params["Receipt"] = encoded_receipt
    return params


def build_payment_url(*args, **kwargs):
    """Legacy test-mode GET URL (without receipt)."""
    params = build_payment_fields(*args, **kwargs)
    return f"{PAYMENT_URL}?{urlencode(params)}"


def verify_result_signature(
    amount,
    order_id,
    received_signature,
    password2,
    algorithm="sha256",
):
    if not received_signature:
        return False

    # Live ResultURL commonly sends OutSum with SIX decimal places (e.g. 2999.000000).
    # Preserve its exact textual representation for the signature, but enforce
    # that the value agrees with a ruble/kopeck amount.
    try:
        received = Decimal(str(amount))
        if (not received.is_finite() or received <= 0
                or received != received.quantize(Decimal("0.01"))):
            return False
    except (InvalidOperation, ValueError, TypeError):
        return False

    expected = make_signature(
        [amount, order_id, password2],
        algorithm,
    )

    return hmac.compare_digest(
        expected.lower(),
        received_signature.strip().lower(),
    )
