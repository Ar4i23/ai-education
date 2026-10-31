"""Regression tests for live checkout, signatures, and invoice reconciliation."""
import hashlib
import hmac
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit, unquote

from integrations import robokassa
from integrations.payment_callback import process_payment_notification
from integrations.access_delivery import init_delivery_table
from integrations.robokassa import make_signature
from integrations import robokassa_checkout as checkout


class PaymentLinkTests(unittest.TestCase):
    def setUp(self):
        self.fake_order = dict(id=17, amount='2999.00', currency='RUB',
                               status='pending', idempotence_key='a'*32)
        self.env_patch = patch.dict('os.environ', {
            'ROBOKASSA_MERCHANT_LOGIN': 'antonovaai',
            'ROBOKASSA_HASH_ALGORITHM': 'sha256',
            'ROBOKASSA_TEST_MODE': 'false',
            'ROBOKASSA_LIVE_PASSWORD1': 'live1fake',
            'ROBOKASSA_LIVE_PASSWORD2': 'live2fake',
            'ROBOKASSA_USE_RECEIPT': 'true',
            'ROBOKASSA_RECEIPT_TAX': 'none',
            'ROBOKASSA_INVOICE_OFFSET': '2000000000',
            'ROBOKASSA_CHECKOUT_SECRET': 'c'*64,
            'PUBLIC_BASE_URL': 'https://pay.example.test',
        })
        self.env_patch.start()
        self.mode_patch = patch.object(checkout, 'PAYMENT_MODE', 'robokassa_live')
        self.mode_patch.start()

    def tearDown(self):
        self.mode_patch.stop()
        self.env_patch.stop()

    def test_signed_receipt_full_price_and_promo(self):
        for amount in ('3499.00', '2999.00'):
            with self.subTest(amount=amount):
                order = {**self.fake_order, 'amount':amount}
                fields = checkout.build_fields(order)
                self.assertEqual(fields['OutSum'], amount)
                self.assertEqual(fields['InvId'], '2000000017')
                self.assertEqual(fields['IsTest'], '0')
                receipt = json.loads(unquote(fields['Receipt']))
                self.assertEqual(receipt['items'][0]['sum'], float(amount))
                expected = make_signature(['antonovaai', amount, '2000000017',
                                           fields['Receipt'], 'live1fake'])
                self.assertEqual(fields['SignatureValue'], expected)

    def test_signed_payment_link_and_replay(self):
        url = checkout.checkout_url(self.fake_order)
        q = parse_qs(urlsplit(url).query)
        self.assertTrue(checkout.verify_checkout_token(
            self.fake_order, q['expires'][0], q['token'][0]))
        self.assertFalse(checkout.verify_checkout_token(
            {**self.fake_order, 'idempotence_key':'other'},
            q['expires'][0], q['token'][0]))
        self.assertFalse(checkout.verify_checkout_token(
            self.fake_order, '1', q['token'][0]))
        self.assertEqual(urlsplit(url).hostname, 'pay.example.test')

    def test_live_refuses_no_receipt(self):
        with patch.dict('os.environ', {'ROBOKASSA_USE_RECEIPT':'false'}):
            self.assertFalse(checkout.checkout_ready())
            with self.assertRaises(ValueError):
                checkout.checkout_url(self.fake_order)


class CallbackProductionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dbpath = Path(self.tmp.name)/'sample.db'
        with sqlite3.connect(self.dbpath) as con:
            con.executescript('''
            CREATE TABLE users (id INTEGER PRIMARY KEY, telegram_id INTEGER NOT NULL);
            CREATE TABLE orders (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL,
              amount TEXT NOT NULL, currency TEXT NOT NULL, status TEXT NOT NULL,
              payment_id TEXT);
            INSERT INTO users(id,telegram_id) VALUES (1,123456789);
            INSERT INTO orders(id,user_id,amount,currency,status)
              VALUES (17,1,'2999.00','RUB','pending');
            ''')
        def connection(): return sqlite3.connect(self.dbpath)
        with patch('integrations.access_delivery.get_connection', side_effect=connection):
            init_delivery_table()
        self.db_patch = patch('integrations.payment_callback.get_connection',side_effect=connection)
        self.db_patch.start()

    def tearDown(self):
        self.db_patch.stop()
        self.tmp.cleanup()

    def test_six_decimal_live_amount_and_idempotency(self):
        sig=make_signature(['2999.000000', '2000000017', 'live2fake'])
        kwargs = dict(out_sum='2999.000000', inv_id='2000000017',
                      signature=sig, password2='live2fake', invoice_offset=2000000000)
        first = process_payment_notification(**kwargs)
        second = process_payment_notification(**kwargs)
        self.assertTrue(first.accepted)
        self.assertTrue(first.newly_paid)
        self.assertTrue(second.accepted)
        self.assertFalse(second.newly_paid)
        with sqlite3.connect(self.dbpath) as con:
            self.assertEqual(con.execute('SELECT payment_id FROM orders WHERE id=17').fetchone()[0],
                             'robokassa:2000000017')

    def test_invalid_live_amount_rejected(self):
        sig=make_signature(['2999.001000','2000000017','live2fake'])
        result = process_payment_notification(out_sum='2999.001000',
                    inv_id='2000000017',signature=sig,
                    password2='live2fake',invoice_offset=2000000000)
        self.assertFalse(result.accepted)
