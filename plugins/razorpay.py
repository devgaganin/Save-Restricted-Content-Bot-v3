"""Optional payment links. Keep disabled until compliance and provider testing."""
import logging
import os
import secrets
from datetime import datetime
from urllib.parse import urlsplit
import aiohttp
from pyrogram import filters
from pyrogram.enums import ParseMode
from pyrogram.types import InlineKeyboardButton as Button, InlineKeyboardMarkup as Keyboard
from config import P0
from shared_client import app
from utils.func import db, premium_users_collection
from utils.billing import check_premium_indexes_async
from plugins.pay import DURATION_DAYS

logger = logging.getLogger(__name__)
links = db['razorpay_payments']


def is_configured():
    return (os.getenv('RAZORPAY_ENABLED', '').lower() == 'true'
            and all(os.getenv(k) for k in ('RAZORPAY_KEY_ID', 'RAZORPAY_KEY_SECRET', 'RAZORPAY_WEBHOOK_SECRET')))


def plan_amount(key):
    try:
        amount = int(os.getenv(f'RAZORPAY_PLAN_{key.upper()}_PAISE', '0'))
        return amount if amount > 0 else 0
    except ValueError:
        return 0


async def show_razorpay(message):
    if not is_configured():
        await message.edit_text('💳 <b>𝗥𝗔𝗭𝗢𝗥𝗣𝗔𝗬</b>\n\n<blockquote>⚠️ <b>Not configured. No payment will be collected.</b></blockquote>', reply_markup=Keyboard([[Button('🔙 𝗕𝗔𝗖𝗞', callback_data='ui:premium')]]), parse_mode=ParseMode.HTML)
        return
    rows = []
    for key, plan in P0.items():
        amount = plan_amount(key)
        if amount and plan['du'] > 0 and plan['u'] in DURATION_DAYS:
            rows.append([Button(f"💳 {plan['l']} — ₹{amount / 100:.2f}", callback_data=f'rzp:{key}')])
    rows.append([Button('🔙 𝗕𝗔𝗖𝗞', callback_data='ui:premium')])
    await message.edit_text('💳 <b>𝗥𝗔𝗭𝗢𝗥𝗣𝗔𝗬</b>\n\n<blockquote>Choose a plan. Activation follows provider verification.</blockquote>', reply_markup=Keyboard(rows), parse_mode=ParseMode.HTML)


@app.on_callback_query(filters.regex(r'^rzp:(d|w|m)$'))
async def create_payment_link(client, query):
    if not query.message or query.message.chat.type.value != 'private' or not is_configured():
        await query.answer('Payment is not available.', show_alert=True)
        return
    key = query.data.split(':', 1)[1]
    plan = P0[key]
    amount = plan_amount(key)
    if not amount or plan['u'] not in DURATION_DAYS or plan['du'] <= 0:
        await query.answer('Plan is not configured.', show_alert=True)
        return
    await query.answer('Creating payment link…')
    reference = secrets.token_hex(16)
    invoice = {'_id': reference, 'user_id': query.from_user.id, 'amount': amount, 'currency': 'INR',
               'days': plan['du'] * DURATION_DAYS[plan['u']], 'status': 'creating', 'created_at': datetime.utcnow()}
    try:
        await check_premium_indexes_async(premium_users_collection)
        await premium_users_collection.create_index('user_id', unique=True)
        await links.create_index('payment_id', unique=True, sparse=True)
        await links.insert_one(invoice)
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout, auth=aiohttp.BasicAuth(os.environ['RAZORPAY_KEY_ID'], os.environ['RAZORPAY_KEY_SECRET'])) as session:
            async with session.post('https://api.razorpay.com/v1/payment_links', json={
                'amount': amount, 'currency': 'INR', 'accept_partial': False, 'reference_id': reference,
                'description': f"Premium {plan['l']} ({invoice['days']} days)",
                'notify': {'sms': False, 'email': False}, 'reminder_enable': False,
            }) as response:
                response.raise_for_status()
                link = await response.json()
        if not link.get('id') or urlsplit(link.get('short_url', '')).scheme != 'https':
            raise ValueError('Provider returned invalid link')
        await links.update_one({'_id': reference, 'status': 'creating'}, {'$set': {'status': 'created', 'provider_link_id': link['id']}})
        await query.message.reply_text('💳 <b>𝗣𝗔𝗬𝗠𝗘𝗡𝗧 𝗟𝗜𝗡𝗞</b>\n\n<blockquote>Complete payment at Razorpay. Premium activates after the signed webhook is verified.</blockquote>',
            reply_markup=Keyboard([[Button('💳 𝗣𝗔𝗬', url=link['short_url'])], [Button('🔙 𝗕𝗔𝗖𝗞', callback_data='ui:premium')]]), parse_mode=ParseMode.HTML)
    except Exception:
        logger.exception('Razorpay payment link creation failed')
        await query.message.reply_text('⚠️ Could not create a payment link. Try again later.')
