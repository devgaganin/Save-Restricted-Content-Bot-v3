"""Optional manual gift-card references. Never request or store redemption codes."""
import html
import logging
import os
import re
from datetime import datetime
from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError
from pyrogram import filters
from pyrogram.enums import ParseMode
from pyrogram.types import InlineKeyboardButton as Button, InlineKeyboardMarkup as Keyboard
from config import OWNER_ID, P0
from shared_client import app
from utils.func import db, premium_users_collection
from plugins.pay import DURATION_DAYS, grant_invoice

logger = logging.getLogger(__name__)
requests = db['gift_requests']
pending = {}
CARD_TYPES = {'amazon': 'Amazon', 'google': 'Google Play', 'apple': 'Apple', 'steam': 'Steam', 'flipkart': 'Flipkart', 'playstation': 'PlayStation', 'netflix': 'Netflix', 'spotify': 'Spotify'}


def enabled():
    return os.getenv('GIFT_ENABLED', '').lower() == 'true' and bool(OWNER_ID)


async def show_gift(message):
    if not enabled():
        await message.edit_text('🎁 <b>𝗚𝗜𝗙𝗧 𝗖𝗔𝗥𝗗</b>\n\n<blockquote>⚠️ <b>Manual gift-card payment is not configured. Do not send redemption codes.</b></blockquote>', reply_markup=Keyboard([[Button('🔙 𝗕𝗔𝗖𝗞', callback_data='ui:premium')]]), parse_mode=ParseMode.HTML)
        return
    rows = []
    for key, plan in P0.items():
        if plan['du'] > 0 and plan['u'] in DURATION_DAYS:
            value = os.getenv(f'GIFT_PLAN_{key.upper()}_VALUE', 'Contact support')[:40]
            rows.append([Button(f"🎁 {plan['l']} — {value}", callback_data=f'gift:plan:{key}')])
    rows.append([Button('🔙 𝗕𝗔𝗖𝗞', callback_data='ui:premium')])
    await message.edit_text('🎁 <b>𝗠𝗔𝗡𝗨𝗔𝗟 𝗚𝗜𝗙𝗧 𝗖𝗔𝗥𝗗</b>\n\n<blockquote>Choose a plan. Verification is manual; never send gift-card redemption codes here.</blockquote>', reply_markup=Keyboard(rows), parse_mode=ParseMode.HTML)


async def show_pending(message):
    rows = []
    async for doc in requests.find({'status': {'$in': ['pending', 'processing']}}).sort('created_at', -1).limit(10):
        rows.append([Button(f"🎁 {doc.get('user_id')} • {doc.get('status')}", callback_data=f"gift:view:{doc['_id']}")])
    rows.append([Button('🔙 𝗕𝗔𝗖𝗞', callback_data='ui:owner')])
    await message.edit_text('🎁 <b>𝗣𝗘𝗡𝗗𝗜𝗡𝗚 𝗥𝗘𝗩𝗜𝗘𝗪</b>\n\n<blockquote>Select a request. Verify payment independently before approval.</blockquote>', reply_markup=Keyboard(rows), parse_mode=ParseMode.HTML)


@app.on_callback_query(filters.regex(r'^gift:'))
async def gift_callback(client, query):
    if not query.message or query.message.chat.type.value != 'private' or not enabled():
        await query.answer('Unavailable.', show_alert=True)
        return
    parts = query.data.split(':')
    uid = query.from_user.id
    if len(parts) == 3 and parts[1] == 'plan' and parts[2] in P0:
        key = parts[2]
        rows = [[Button(f'🎁 {name}', callback_data=f'gift:type:{key}:{card}')] for card, name in CARD_TYPES.items()]
        rows.append([Button('🔙 𝗕𝗔𝗖𝗞', callback_data='ui:gift')])
        await query.answer()
        await query.message.edit_text('🎁 <b>𝗖𝗛𝗢𝗢𝗦𝗘 𝗖𝗔𝗥𝗗 𝗧𝗬𝗣𝗘</b>\n\n<blockquote>🛡️ Never send a redemption code to this bot.</blockquote>', reply_markup=Keyboard(rows), parse_mode=ParseMode.HTML)
    elif len(parts) == 4 and parts[1] == 'type' and parts[2] in P0 and parts[3] in CARD_TYPES:
        from plugins.menu import waiting, busy_inputs, markup, page
        from plugins import batch
        from utils.custom_filters import get_user_step
        if uid in batch.Z or uid in waiting or uid in busy_inputs or get_user_step(uid):
            await query.answer('Cancel the active flow first.', show_alert=True)
            return
        pending[uid] = {'plan': parts[2], 'card': parts[3]}
        waiting[uid] = 'gift_reference'
        await query.answer()
        await query.message.reply_text(page('🎁 𝗣𝗔𝗬𝗠𝗘𝗡𝗧 𝗥𝗘𝗙𝗘𝗥𝗘𝗡𝗖𝗘', 'Send a non-secret reference in the form REF- followed by 6–32 letters or digits. Do not send card numbers or redemption codes. The owner must independently verify your payment.'), reply_markup=markup([[('❌ 𝗖𝗔𝗡𝗖𝗘𝗟', 'cancel')]], 'premium'), parse_mode=ParseMode.HTML)
    elif len(parts) == 3 and parts[1] in ('view', 'approve', 'reject'):
        if uid not in OWNER_ID:
            await query.answer('Owner only.', show_alert=True)
            return
        try:
            oid = ObjectId(parts[2])
        except Exception:
            await query.answer('Invalid request.', show_alert=True)
            return
        if parts[1] == 'view':
            doc = await requests.find_one({'_id': oid, 'status': {'$in': ['pending', 'processing']}})
            if not doc:
                await query.answer('Request no longer pending.', show_alert=True)
                return
            await query.answer()
            body = f"🎁 <b>𝗥𝗘𝗩𝗜𝗘𝗪</b>\n\n<blockquote>👤 User: <code>{doc['user_id']}</code>\n📅 Days: {doc['days']}\n🎁 Card: {html.escape(doc['card_type'])}\n🧾 Reference: <code>{html.escape(doc['reference'])}</code>\n⚠️ Verify independently.</blockquote>"
            kb = Keyboard([[Button('✅ 𝗔𝗣𝗣𝗥𝗢𝗩𝗘', callback_data=f'gift:approve:{oid}'), Button('❌ 𝗥𝗘𝗝𝗘𝗖𝗧', callback_data=f'gift:reject:{oid}')], [Button('🔙 𝗕𝗔𝗖𝗞', callback_data='ui:owner_gifts')]])
            await query.message.edit_text(body, reply_markup=kb, parse_mode=ParseMode.HTML)
            return
        if parts[1] == 'reject':
            doc = await requests.find_one_and_update({'_id': oid, 'status': 'pending'}, {'$set': {'status': 'rejected', 'reviewed_by': uid, 'reviewed_at': datetime.utcnow()}}, return_document=ReturnDocument.AFTER)
            await query.answer('Rejected.' if doc else 'Already processed.', show_alert=True)
            if doc:
                try: await client.send_message(doc['user_id'], '⚠️ Your manual payment request was rejected. Contact support for details.')
                except Exception: logger.warning('Could not notify user of rejected gift request')
            return
        try:
            doc = await requests.find_one_and_update({'_id': oid, 'status': {'$in': ['pending', 'processing']}}, {'$set': {'status': 'processing', 'reviewed_by': uid}}, return_document=ReturnDocument.AFTER)
            if not doc:
                await query.answer('Already processed.', show_alert=True)
                return
            await premium_users_collection.create_index('user_id', unique=True)
            charge = 'gift:' + str(oid)
            result = await grant_invoice(doc, charge)
            if not result or charge not in result.get('applied_payments', []):
                raise RuntimeError('Grant not confirmed')
            await requests.update_one({'_id': oid, 'status': 'processing'}, {'$set': {'status': 'approved', 'reviewed_at': datetime.utcnow()}})
            await query.answer('Approved.', show_alert=True)
            try:
                await client.send_message(doc['user_id'], '✅ <b>𝗣𝗥𝗘𝗠𝗜𝗨𝗠 𝗔𝗖𝗧𝗜𝗩𝗔𝗧𝗘𝗗</b>\n\n<blockquote>Your manual payment was approved.</blockquote>', parse_mode=ParseMode.HTML)
            except Exception:
                logger.warning('Could not notify gift buyer %s', doc['user_id'])
        except Exception:
            logger.exception('Gift approval failed')
            await query.answer('Could not activate; retry or review manually.', show_alert=True)
    else:
        await query.answer('Invalid request.', show_alert=True)


async def submit_reference(client, message, uid, text):
    data = pending.get(uid)
    reference = text.upper()
    if not data or not re.fullmatch(r'REF-[A-Z0-9]{6,32}', reference):
        await message.reply_text('⚠️ Send a non-secret reference like REF-ABC12345. Never send a redemption code.')
        return False
    plan = P0[data['plan']]
    if plan['du'] <= 0 or plan['u'] not in DURATION_DAYS:
        await message.reply_text('⚠️ This plan is unavailable.')
        return True
    doc = {'user_id': uid, 'plan': data['plan'], 'card_type': data['card'], 'reference': reference,
           'days': plan['du'] * DURATION_DAYS[plan['u']], 'status': 'pending', 'created_at': datetime.utcnow()}
    try:
        await requests.create_index('reference', unique=True)
        result = await requests.insert_one(doc)
    except DuplicateKeyError:
        await message.reply_text('⚠️ This reference was already submitted. Contact support.')
        return True
    pending.pop(uid, None)
    await message.reply_text('✅ Request submitted for manual verification. Activation is not automatic.')
    keyboard = Keyboard([[Button('✅ 𝗔𝗣𝗣𝗥𝗢𝗩𝗘', callback_data=f'gift:approve:{result.inserted_id}'), Button('❌ 𝗥𝗘𝗝𝗘𝗖𝗧', callback_data=f'gift:reject:{result.inserted_id}')]])
    for owner in OWNER_ID:
        try:
            body = f"🎁 <b>𝗠𝗔𝗡𝗨𝗔𝗟 𝗥𝗘𝗩𝗜𝗘𝗪</b>\n\n<blockquote>👤 User: <code>{uid}</code>\n💎 Plan: {html.escape(plan['l'])}\n🎁 Card: {html.escape(CARD_TYPES[data['card']])}\n🧾 Reference: <code>{html.escape(reference)}</code>\n⚠️ Independently verify payment before approval.</blockquote>"
            await client.send_message(owner, body, reply_markup=keyboard, parse_mode=ParseMode.HTML)
        except Exception:
            logger.exception('Could not notify owner about gift request')
    return True
