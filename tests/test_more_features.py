import hashlib
import hmac
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault('API_ID', '12345')
os.environ.setdefault('API_HASH', 'test-api-hash')
os.environ.setdefault('BOT_TOKEN', '12345:test-token')
os.environ.setdefault('MONGO_DB', 'mongodb://localhost:27017')

from plugins import batch, gift
from utils.razorpay import valid_signature, paid_event_details
import app as web


def event():
    return {'event': 'payment_link.paid', 'payload': {
        'payment_link': {'entity': {'id': 'plink_123', 'reference_id': 'ref-1', 'status': 'paid', 'amount': 10000, 'amount_paid': 10000, 'currency': 'INR'}},
        'payment': {'entity': {'id': 'pay_123', 'status': 'captured', 'amount': 10000, 'currency': 'INR'}},
    }}


class ForwardTests(unittest.IsolatedAsyncioTestCase):
    async def test_public_message_lookup_does_not_require_preexisting_cache(self):
        expected = SimpleNamespace(empty=False)
        bot = SimpleNamespace(get_messages=AsyncMock(return_value=expected))
        user = SimpleNamespace(get_messages=AsyncMock())
        self.assertIs(await batch.get_msg(bot, user, 'example', 123, 'public'), expected)
        bot.get_messages.assert_awaited_once_with('example', 123)
        user.get_messages.assert_not_awaited()

    async def test_flood_wait_is_honoured_and_retried(self):
        from pyrogram.errors import FloodWait
        expected = SimpleNamespace(empty=False)
        bot = SimpleNamespace(get_messages=AsyncMock(side_effect=[FloodWait(value=2), expected]))
        with patch.object(batch.asyncio, 'sleep', new_callable=AsyncMock) as sleep:
            result = await batch.get_msg(bot, None, 'example', 123, 'public')
        self.assertIs(result, expected)
        self.assertEqual(sleep.await_count, 2)

    async def test_private_uses_only_user_and_does_not_auto_join(self):
        expected = SimpleNamespace(empty=False)
        bot = SimpleNamespace(get_messages=AsyncMock())
        user = SimpleNamespace(get_messages=AsyncMock(return_value=expected))
        self.assertIs(await batch.get_msg(bot, user, -100123, 4, 'private'), expected)
        bot.get_messages.assert_not_awaited()

    async def test_pause_resume_and_cancel(self):
        batch.ACTIVE_USERS['123'] = {'total': 2, 'cancel_requested': False}
        with patch.object(batch, 'save_active_users_to_file', new_callable=AsyncMock):
            self.assertTrue(await batch.set_batch_paused(123, True))
            self.assertTrue(batch.get_batch_info(123)['paused'])
            self.assertTrue(await batch.request_batch_cancel(123))
            self.assertFalse(await batch.set_batch_paused(123, False))
            await batch.remove_active_batch(123)

    async def test_gift_reference_rejects_redemption_code(self):
        gift.pending[123] = {'plan': 'd', 'card': 'amazon'}
        msg = SimpleNamespace(reply_text=AsyncMock())
        self.assertFalse(await gift.submit_reference(None, msg, 123, 'GIFT-CODE-123456789'))
        gift.pending.clear()


class RazorpayTests(unittest.TestCase):
    def test_signature_uses_exact_body(self):
        body = b'{"hello":1}'
        sig = hmac.new(b'secret', body, hashlib.sha256).hexdigest()
        self.assertTrue(valid_signature('secret', body, sig))
        self.assertFalse(valid_signature('secret', body + b' ', sig))
        self.assertFalse(valid_signature('', body, sig))

    def test_reject_partial_and_non_captured_payment(self):
        e = event()
        self.assertIsNotNone(paid_event_details(e))
        e['payload']['payment_link']['entity']['amount_paid'] = 1
        self.assertIsNone(paid_event_details(e))
        e = event()
        e['payload']['payment']['entity']['status'] = 'authorized'
        self.assertIsNone(paid_event_details(e))

    def post(self, payload, secret='secret', signature=None):
        body = json.dumps(payload).encode()
        sig = signature or hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        with web.app.test_client() as client:
            return client.post('/webhooks/razorpay', data=body, headers={'X-Razorpay-Signature': sig, 'Content-Type': 'application/json'})

    def test_webhook_rejects_bad_signature_without_db(self):
        with patch.dict(os.environ, {'RAZORPAY_ENABLED': 'true', 'RAZORPAY_WEBHOOK_SECRET': 'secret'}), patch.object(web, 'MongoClient') as mongo:
            response = self.post(event(), signature='0' * 64)
        self.assertEqual(response.status_code, 401)
        mongo.assert_not_called()

    def test_webhook_checks_amount_before_granting(self):
        invoice = {'_id': 'ref-1', 'status': 'created', 'provider_link_id': 'plink_123', 'amount': 999, 'currency': 'INR', 'user_id': 123, 'days': 7}
        db = MagicMock()
        collections = {'razorpay_payments': MagicMock(), 'premium_users': MagicMock()}
        db.__getitem__.side_effect = collections.__getitem__
        db['razorpay_payments'].find_one.return_value = invoice
        mongo = MagicMock()
        mongo.__getitem__.return_value = db
        with patch.dict(os.environ, {'RAZORPAY_ENABLED': 'true', 'RAZORPAY_WEBHOOK_SECRET': 'secret'}), patch.object(web, 'MongoClient', return_value=mongo):
            response = self.post(event())
        self.assertEqual(response.status_code, 400)
        db['premium_users'].update_one.assert_not_called()

    def test_duplicate_completed_webhook_does_not_grant_again(self):
        invoice = {'_id': 'ref-1', 'status': 'completed', 'payment_id': 'pay_123', 'provider_link_id': 'plink_123', 'amount': 10000, 'currency': 'INR', 'user_id': 123, 'days': 7}
        db = MagicMock()
        collections = {'razorpay_payments': MagicMock(), 'premium_users': MagicMock()}
        db.__getitem__.side_effect = collections.__getitem__
        db['razorpay_payments'].find_one.return_value = invoice
        mongo = MagicMock()
        mongo.__getitem__.return_value = db
        with patch.dict(os.environ, {'RAZORPAY_ENABLED': 'true', 'RAZORPAY_WEBHOOK_SECRET': 'secret'}), patch.object(web, 'MongoClient', return_value=mongo):
            response = self.post(event())
        self.assertEqual(response.status_code, 200)
        db['premium_users'].update_one.assert_not_called()

    def test_webhook_retries_if_premium_has_ttl_index(self):
        invoice = {'_id': 'ref-1', 'status': 'created', 'provider_link_id': 'plink_123', 'amount': 10000, 'currency': 'INR', 'user_id': 123, 'days': 7}
        collections = {'razorpay_payments': MagicMock(), 'premium_users': MagicMock()}
        db = MagicMock()
        db.__getitem__.side_effect = collections.__getitem__
        collections['razorpay_payments'].find_one.return_value = invoice
        collections['premium_users'].index_information.return_value = {'expireAt_1': {'expireAfterSeconds': 0}}
        mongo = MagicMock()
        mongo.__getitem__.return_value = db
        with patch.dict(os.environ, {'RAZORPAY_ENABLED': 'true', 'RAZORPAY_WEBHOOK_SECRET': 'secret'}), patch.object(web, 'MongoClient', return_value=mongo), self.assertLogs(web.app.logger, level='ERROR'):
            response = self.post(event())
        self.assertEqual(response.status_code, 503)
        collections['premium_users'].update_one.assert_not_called()
        collections['razorpay_payments'].find_one_and_update.assert_not_called()

    def test_webhook_grants_and_marks_complete(self):
        invoice = {'_id': 'ref-1', 'status': 'created', 'provider_link_id': 'plink_123', 'amount': 10000, 'currency': 'INR', 'user_id': 123, 'days': 7}
        db = MagicMock()
        collections = {'razorpay_payments': MagicMock(), 'premium_users': MagicMock()}
        db.__getitem__.side_effect = collections.__getitem__
        links, premium = db['razorpay_payments'], db['premium_users']
        links.find_one.return_value = invoice
        links.find_one_and_update.return_value = invoice
        premium.find_one.return_value = {'user_id': 123, 'applied_payments': ['razorpay:pay_123']}
        mongo = MagicMock()
        mongo.__getitem__.return_value = db
        with patch.dict(os.environ, {'RAZORPAY_ENABLED': 'true', 'RAZORPAY_WEBHOOK_SECRET': 'secret'}), patch.object(web, 'MongoClient', return_value=mongo):
            response = self.post(event())
        self.assertEqual(response.status_code, 200)
        selector = premium.update_one.call_args.args[0]
        self.assertEqual(selector['applied_payments'], {'$ne': 'razorpay:pay_123'})
        self.assertEqual(links.update_one.call_args.args[1]['$set']['status'], 'completed')


class MirrorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from plugins import mirror
        self.mirror = mirror
        mirror.active.clear()

    async def test_mirror_rejects_same_source_destination(self):
        mirror = self.mirror
        client = SimpleNamespace(get_chat=AsyncMock(return_value=SimpleNamespace(id=-100999)), add_handler=MagicMock())
        data = {'session_string': 'encrypted', 'bot_token': 'encrypted', 'chat_id': '-100999'}
        with patch.object(mirror, 'OWNER_ID', [123]), patch.object(mirror, 'get_user_data', new_callable=AsyncMock, return_value=data), patch.object(mirror.batch, 'get_ubot', new_callable=AsyncMock, return_value=object()), patch.object(mirror.batch, 'get_uclient', new_callable=AsyncMock, return_value=client), patch.dict(mirror.batch.UC, {123: client}):
            ok, reason = await mirror.start_mirror(123, '@source')
        self.assertFalse(ok)
        self.assertIn('different', reason)
        client.add_handler.assert_not_called()

    async def test_mirror_uses_destination_snapshot_and_can_stop(self):
        mirror = self.mirror
        client = SimpleNamespace(get_chat=AsyncMock(return_value=SimpleNamespace(id=-100999)), add_handler=MagicMock(), remove_handler=MagicMock())
        bot = object()
        data = {'session_string': 'encrypted', 'bot_token': 'encrypted', 'chat_id': '-100888/4'}
        with patch.object(mirror, 'OWNER_ID', [123]), patch.object(mirror, 'get_user_data', new_callable=AsyncMock, return_value=data), patch.object(mirror.batch, 'get_ubot', new_callable=AsyncMock, return_value=bot), patch.object(mirror.batch, 'get_uclient', new_callable=AsyncMock, return_value=client), patch.dict(mirror.batch.UC, {123: client}), patch.object(mirror.mirrors, 'create_index', new_callable=AsyncMock), patch.object(mirror.mirrors, 'update_one', new_callable=AsyncMock), patch.object(mirror.batch, 'process_msg', new_callable=AsyncMock, return_value='Done.') as process:
            ok, _ = await mirror.start_mirror(123, '@source')
            self.assertTrue(ok)
            handler = client.add_handler.call_args.args[0]
            await handler.original_callback(client, SimpleNamespace(id=22))
            self.assertEqual(process.call_args.kwargs['destination_override'], '-100888/4')
            self.assertTrue(await mirror.stop_mirror(123))
        client.remove_handler.assert_called_once()

    async def test_gift_approval_is_owner_only(self):
        msg = SimpleNamespace(chat=SimpleNamespace(type=SimpleNamespace(value='private')))
        query = SimpleNamespace(message=msg, from_user=SimpleNamespace(id=123), data='gift:approve:aaaaaaaaaaaaaaaaaaaaaaaa', answer=AsyncMock())
        with patch.object(gift, 'enabled', return_value=True), patch.object(gift, 'OWNER_ID', [999]), patch.object(gift.requests, 'find_one_and_update', new_callable=AsyncMock) as update:
            await gift.gift_callback(None, query)
        update.assert_not_awaited()
        self.assertTrue(query.answer.call_args.kwargs['show_alert'])


if __name__ == '__main__':
    unittest.main()
