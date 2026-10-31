import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from handlers import _payment_amount_matches


class PaymentAmountTests(unittest.TestCase):
    def test_accepts_expected_ruble_amount(self):
        payment = {"amount": {"value": "2499.00", "currency": "RUB"}}
        self.assertTrue(_payment_amount_matches(payment, "2499"))

    def test_rejects_test_amount_after_price_is_restored(self):
        payment = {"amount": {"value": "10.00", "currency": "RUB"}}
        self.assertFalse(_payment_amount_matches(payment, "2499"))

    def test_rejects_wrong_currency(self):
        payment = {"amount": {"value": "2499.00", "currency": "USD"}}
        self.assertFalse(_payment_amount_matches(payment, "2499"))

    def test_rejects_missing_or_invalid_amount(self):
        self.assertFalse(_payment_amount_matches({}, "2499"))
        self.assertFalse(
            _payment_amount_matches(
                {"amount": {"value": "not-a-number", "currency": "RUB"}},
                "2499",
            )
        )


if __name__ == "__main__":
    unittest.main()
