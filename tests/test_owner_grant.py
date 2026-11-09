"""Offline manual premium grant, storage safety, and owner review tests."""
import os
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from bson import ObjectId
from pymongo.errors import DuplicateKeyError

os.environ.setdefault('API_ID', '12345')
os.environ.setdefault('API_HASH', 'test-api-hash')
os.environ.setdefault('BOT_TOKEN', '12345:test-token')
os.environ.setdefault('MONGO_DB', 'mongodb://localhost:27017')

from plugins import batch, batch_queue, menu, owner_grant, pay
from utils import func
from utils.custom_filters import user_steps


class PremiumStore:
    """Small fake applying the tested grant pipeline; not a MongoDB emulator."""
    def __init__(self):
        self.docs = {}
        self.indexes = {'_id_': {'key': [('_id', 1)]}}
        self.fail_create = None
        self.fail_find = False
        self.writes = 0

    async def index_information(self):
        return self.indexes

    async def create_index(self, *args, **kwargs):
        if self.fail_create: raise self.fail_create
        return 'user_id_1'

    async def update_one(self, selector, pipeline, upsert=False):
        uid = selector['user_id']; charge = selector['applied_payments']['$ne']
        doc = self.docs.get(uid)
        if doc and charge in doc.get('applied_payments', []):
            if upsert: raise DuplicateKeyError('duplicate')
            return SimpleNamespace(matched_count=0)
        self.writes += 1
        doc = dict(doc or {'user_id': uid})
        fields = pipeline[0]['$set']
        now = fields['subscription_start']['$ifNull'][1]
        ms = fields['subscription_end']['$add'][1]
        self.assert_pipeline(fields, charge)
        doc['subscription_start'] = doc.get('subscription_start') or now
        doc['subscription_end'] = max(doc.get('subscription_end') or now, now) + timedelta(milliseconds=ms)
        doc['expireAt'] = doc['subscription_end']
        doc['applied_payments'] = doc.get('applied_payments', []) + [charge]
        self.docs[uid] = doc
        return SimpleNamespace(matched_count=1)

    @staticmethod
    def assert_pipeline(fields, charge):
        assert fields['applied_payments']['$concatArrays'][1] == [charge]
        assert '$max' in fields['subscription_end']['$add'][0]
        assert fields['expireAt'] == fields['subscription_end']

    async def find_one(self, selector, *args):
        if self.fail_find: raise RuntimeError('database unavailable')
        doc = self.docs.get(selector['user_id'])
        if doc and isinstance(selector.get('applied_payments'), str) and selector['applied_payments'] not in doc.get('applied_payments', []):
            return None
        return dict(doc) if doc else None


class AuditStore:
    def __init__(self):
        self.docs = {}
        self.fail_insert = False
        self.fail_update = False

    async def insert_one(self, doc):
        if self.fail_insert: raise RuntimeError('insert unavailable')
        doc = dict(doc); doc['_id'] = ObjectId()
        self.docs[doc['_id']] = doc
        return SimpleNamespace(inserted_id=doc['_id'], acknowledged=True)

    async def update_one(self, selector, update):
        if self.fail_update: raise RuntimeError('update unavailable')
        doc = self.docs.get(selector['_id'])
        if not doc or doc['owner_id'] != selector['owner_id'] or doc['action'] != selector['action']:
            return SimpleNamespace(acknowledged=True, matched_count=0)
        doc.update(update['$set'])
        return SimpleNamespace(acknowledged=True, matched_count=1)

    async def find_one(self, selector, projection):
        doc = self.docs.get(selector['_id'])
        if not doc or doc['owner_id'] != selector['owner_id'] or doc['action'] != selector['action']:
            return None
        return dict(doc)


def message(text='456 30', uid=123):
    return SimpleNamespace(from_user=SimpleNamespace(id=uid), text=text, photo=None,
                           reply_text=AsyncMock(), stop_propagation=MagicMock())


def query(action, uid=123):
    msg = SimpleNamespace(chat=SimpleNamespace(id=uid, type=SimpleNamespace(value='private')),
                          reply_text=AsyncMock(), edit_text=AsyncMock())
    return SimpleNamespace(from_user=SimpleNamespace(id=uid), message=msg, data='ui:' + action, answer=AsyncMock())


class OwnerGrantTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        menu.waiting.clear(); menu.busy_inputs.clear(); batch.Z.clear(); batch.ACTIVE_USERS.clear()
        batch_queue.locks.clear(); user_steps.clear()
        self.premium = PremiumStore(); self.audit = AuditStore()
        self.patches = [patch.object(owner_grant, 'premium_users_collection', self.premium),
                        patch.object(pay, 'premium_users_collection', self.premium),
                        patch.object(func, 'premium_users_collection', self.premium),
                        patch.object(owner_grant, 'db', {'admin_audit': self.audit}),
                        patch.object(menu, 'OWNER_ID', [123]),
                        patch.object(menu.localization, 'language_for', new_callable=AsyncMock, return_value='en')]
        for p in self.patches: p.start(); self.addCleanup(p.stop)

    async def grant(self, text='456 30', client=None):
        menu.waiting[123] = 'owner_grant'
        msg = message(text)
        await menu.menu_input(client, msg)
        return msg

    async def test_new_owner_grant_is_saved_and_recipient_notified(self):
        client = SimpleNamespace(send_message=AsyncMock())
        msg = await self.grant(client=client)
        self.assertTrue(await func.is_premium_user(456))
        self.assertTrue(await func.is_premium_user('456'))
        doc = self.premium.docs[456]; audit_id, audit = next(iter(self.audit.docs.items()))
        self.assertEqual(audit['status'], 'completed'); self.assertIn('manual:' + str(audit_id), doc['applied_payments'])
        self.assertLess(abs((doc['subscription_end'] - datetime.utcnow()).total_seconds() - 30 * 86400), 3)
        text = msg.reply_text.await_args.args[0]
        self.assertIn('recorded for user 456', text); self.assertIn('Active until', text); self.assertIn(str(audit_id), text)
        client.send_message.assert_awaited_once(); self.assertNotIn(123, menu.waiting)

    async def test_extends_active_plan_and_restarts_expired_plan(self):
        for expiry in (datetime.utcnow() + timedelta(days=5), datetime.utcnow() - timedelta(days=5)):
            with self.subTest(expiry=expiry):
                self.premium.docs = {456: {'user_id': 456, 'subscription_end': expiry, 'applied_payments': ['prior-payment']}}
                msg = await self.grant('456 7')
                expected = max(expiry, datetime.utcnow()) + timedelta(days=7)
                self.assertLess(abs((self.premium.docs[456]['subscription_end'] - expected).total_seconds()), 3)
                self.assertIn('prior-payment', self.premium.docs[456]['applied_payments'])
                self.assertIn('recorded', msg.reply_text.await_args.args[0])

    async def test_same_audit_charge_is_idempotent(self):
        invoice = {'user_id': 456, 'days': 7}
        first = await pay.grant_invoice(invoice, 'manual:abc')
        second = await pay.grant_invoice(invoice, 'manual:abc')
        self.assertEqual(first['subscription_end'], second['subscription_end'])
        self.assertEqual(second['applied_payments'], ['manual:abc'])
        self.assertEqual(self.premium.writes, 1)

    async def test_premium_lookup_normalizes_numeric_user_ids_and_rejects_invalid(self):
        self.premium.docs[456] = {'user_id': 456, 'subscription_end': datetime.utcnow() + timedelta(days=1)}
        self.assertTrue(await func.is_premium_user('456'))
        self.assertIsNotNone(await func.get_premium_details('456'))
        for value in ('not-an-id', None, 0, -5):
            self.assertFalse(await func.is_premium_user(value))
            self.assertIsNone(await func.get_premium_details(value))

    async def test_index_inspection_failure_never_creates_audit_or_grant(self):
        with patch.object(self.premium, 'index_information', new_callable=AsyncMock, side_effect=RuntimeError('credential-secret')):
            msg = await self.grant()
        self.assertIn('could not inspect', msg.reply_text.await_args.args[0])
        self.assertNotIn('credential-secret', msg.reply_text.await_args.args[0])
        self.assertEqual(self.premium.writes, 0); self.assertFalse(self.audit.docs)

    async def test_unknown_expiry_is_not_reported_active(self):
        reference = ObjectId()
        with patch.object(pay, 'grant_invoice', new_callable=AsyncMock, return_value={'applied_payments': ['manual:' + str(reference)]}), patch.object(self.audit, 'insert_one', new_callable=AsyncMock, return_value=SimpleNamespace(acknowledged=True, inserted_id=reference)):
            msg = await self.grant()
        self.assertIn('expiry could not be verified', msg.reply_text.await_args.args[0])
        self.assertNotIn('Active until', msg.reply_text.await_args.args[0])

    async def test_ttl_index_blocks_and_explains_without_changing_data(self):
        self.premium.indexes['expireAt_1'] = {'expireAfterSeconds': 0}
        msg = await self.grant()
        self.assertIn('unsafe TTL index', msg.reply_text.await_args.args[0])
        self.assertEqual(self.premium.writes, 0); self.assertFalse(self.audit.docs)
        self.assertIn('expireAt_1', self.premium.indexes)

    async def test_duplicate_ids_and_index_failure_give_no_false_success(self):
        for exc, expected in ((DuplicateKeyError('duplicate'), 'duplicate'), (RuntimeError('private'), 'permissions')):
            with self.subTest(error=exc):
                self.premium.fail_create = exc
                msg = await self.grant()
                self.assertIn(expected, msg.reply_text.await_args.args[0])
                self.assertNotIn('private', msg.reply_text.await_args.args[0])
                self.assertFalse(self.audit.docs); self.assertEqual(self.premium.writes, 0)

    async def test_audit_insert_failure_never_attempts_grant(self):
        self.audit.fail_insert = True
        msg = await self.grant()
        self.assertIn('Premium was not changed', msg.reply_text.await_args.args[0]); self.assertEqual(self.premium.writes, 0)

    async def test_ambiguous_read_after_write_warns_dont_repeat_then_check_grant(self):
        self.premium.fail_find = True
        msg = await self.grant()
        self.assertEqual(self.premium.writes, 1)
        audit_id, audit = next(iter(self.audit.docs.items()))
        self.assertEqual(audit['status'], 'processing')
        self.assertIn('Do not grant again', msg.reply_text.await_args.args[0]); self.assertIn(str(audit_id), msg.reply_text.await_args.args[0])
        self.premium.fail_find = False
        check = message(str(audit_id)); menu.waiting[123] = 'owner_grant_check'
        await menu.menu_input(None, check)
        self.assertIn('recorded in payment history', check.reply_text.await_args.args[0]); self.assertEqual(self.premium.writes, 1)
        self.assertNotIn(123, menu.waiting)

    async def test_missing_grant_evidence_never_claims_safe_to_repeat(self):
        audit_id = ObjectId(); self.audit.docs[audit_id] = {'_id': audit_id, 'owner_id': 123, 'action': 'grant', 'target_id': 456, 'days': 30, 'status': 'processing'}
        msg = message(str(audit_id)); menu.waiting[123] = 'owner_grant_check'
        await menu.menu_input(None, msg)
        self.assertIn('not confirmed', msg.reply_text.await_args.args[0]); self.assertIn('before granting again', msg.reply_text.await_args.args[0])
        self.assertEqual(self.premium.writes, 0)

    async def test_check_scopes_audit_to_initiating_owner(self):
        audit_id = ObjectId(); self.audit.docs[audit_id] = {'_id': audit_id, 'owner_id': 999, 'action': 'grant', 'target_id': 456, 'days': 30}
        msg = message(str(audit_id)); menu.waiting[123] = 'owner_grant_check'
        await menu.menu_input(None, msg)
        self.assertIn('not found', msg.reply_text.await_args.args[0]); self.assertNotIn('456', msg.reply_text.await_args.args[0])

    async def test_audit_update_and_notification_failure_do_not_negate_grant(self):
        self.audit.fail_update = True
        client = SimpleNamespace(send_message=AsyncMock(side_effect=RuntimeError('blocked')))
        msg = await self.grant(client=client)
        text = msg.reply_text.await_args.args[0]
        self.assertIn('Grant of 30 days recorded', text); self.assertIn('audit completion could not be confirmed', text)
        self.assertIn('notification not confirmed', text); self.assertTrue(await func.is_premium_user(456))

    async def test_invalid_inputs_keep_prompt_and_never_write(self):
        for text in ('0 3', '123 -2', '123 0', '123 3651', '123 ٣', '9999999999999999999 1', '@user 10', '123'):
            with self.subTest(text=text):
                msg = await self.grant(text)
                self.assertIn('positive numeric', msg.reply_text.await_args.args[0])
                self.assertEqual(menu.waiting[123], 'owner_grant'); self.assertEqual(self.premium.writes, 0)

    async def test_non_owner_cannot_open_or_submit(self):
        q = query('owner_grant', uid=456); await menu.menu_callback(None, q)
        self.assertTrue(q.answer.await_args.kwargs['show_alert'])
        menu.waiting[456] = 'owner_grant'; msg = message('789 7', uid=456)
        await menu.menu_input(None, msg)
        self.assertIn('Owner only', msg.reply_text.await_args.args[0]); self.assertFalse(self.audit.docs)
        self.assertEqual(self.premium.writes, 0)

    async def test_owner_prompt_check_button_is_localized(self):
        q = query('owner'); await menu.menu_callback(None, q)
        values = [b.callback_data for row in q.message.edit_text.await_args.kwargs['reply_markup'].inline_keyboard for b in row]
        self.assertIn('ui:owner_grant_check', values)
        with patch.object(menu.localization, 'language_for', new_callable=AsyncMock, return_value='hi'):
            q = query('owner_grant_check'); await menu.menu_callback(None, q)
        self.assertIn('संदर्भ', q.message.reply_text.await_args.args[0]); self.assertEqual(menu.waiting[123], 'owner_grant_check')

    async def test_no_generic_retry_if_reply_fails_after_success(self):
        msg = message(); msg.reply_text.side_effect = RuntimeError('telegram unavailable')
        menu.waiting[123] = 'owner_grant'
        with self.assertLogs(owner_grant.logger, level='WARNING'):
            await menu.menu_input(None, msg)
        self.assertTrue(await func.is_premium_user(456)); self.assertEqual(self.premium.writes, 1)
        self.assertEqual(msg.reply_text.await_count, 1); self.assertNotIn(123, menu.waiting)
