"""HTTP edge tests for the real webhook and POST checkout bridge."""
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from aiohttp.test_utils import TestServer, TestClient

from integrations import robokassa_checkout as checkout
from integrations import server as roboserver


class RobokassaHTTPTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.order = dict(id=7, amount='2999.00', currency='RUB',
                          status='pending', idempotence_key='a'*32)
        self.env = patch.dict('os.environ', {
            'ROBOKASSA_MERCHANT_LOGIN':'antonovaai',
            'ROBOKASSA_HASH_ALGORITHM':'sha256',
            'ROBOKASSA_LIVE_PASSWORD1':'secret_for_test_only_1',
            'ROBOKASSA_LIVE_PASSWORD2':'secret_for_test_only_2',
            'ROBOKASSA_TEST_MODE':'false',
            'ROBOKASSA_USE_RECEIPT':'true',
            'ROBOKASSA_RECEIPT_TAX':'none',
            'ROBOKASSA_INVOICE_OFFSET':'2000000000',
            'ROBOKASSA_CHECKOUT_SECRET':'z'*64,
            'PUBLIC_BASE_URL':'https://pay.example.test',
        })
        self.env.start()
        self.mode = patch.object(checkout, 'PAYMENT_MODE', 'robokassa_live')
        self.mode.start()
        self.servermode = patch.object(roboserver, 'PAYMENT_MODE', 'robokassa_live')
        self.servermode.start()
        self.db = patch.object(roboserver, 'get_order', return_value=self.order)
        self.db.start()
        self.client = TestClient(TestServer(roboserver.create_app()))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.db.stop()
        self.servermode.stop()
        self.mode.stop()
        self.env.stop()

    async def test_post_bridge_and_receipt(self):
        url = checkout.checkout_url(self.order)
        part = urlsplit(url)
        response = await self.client.get(part.path + '?' + part.query)
        self.assertEqual(response.status, 200)
        html = await response.text()
        self.assertIn('method="post" action="https://auth.robokassa.ru/Merchant/Index.aspx"', html)
        self.assertIn('name="Receipt"', html)
        self.assertIn('name="OutSum" value="2999.00"', html)
        self.assertIn('name="InvId" value="2000000007"', html)
        self.assertIn('name="IsTest" value="0"', html)
        self.assertNotIn('secret_for_test_only_1', html)

    async def test_bridge_rejects_unsigned_and_paid(self):
        res = await self.client.get('/robokassa/checkout/7?expires=1&token=a')
        self.assertEqual(res.status, 403)
        with patch.object(roboserver, 'get_order', return_value={**self.order,'status':'paid'}):
            url=checkout.checkout_url(self.order)
            part=urlsplit(url)
            res2 = await self.client.get(part.path+'?'+part.query)
            self.assertEqual(res2.status, 404)

    async def test_rejects_bad_notification_fields(self):
        res = await self.client.post('/robokassa/result', data={
            'OutSum':'2999.00', 'InvId':'2000000007',
            'SignatureValue':'bad', 'Shp_fake':'evil'})
        self.assertEqual(res.status, 400)
        res = await self.client.post('/robokassa/result', data={
            'OutSum':'2999.00', 'InvId':'2000000007',
            'SignatureValue':'bad'})
        self.assertEqual(res.status, 400)


if __name__ == '__main__':
    unittest.main()
