"""Private, button-first navigation for the existing bot.

/start remains Telegram's entry point; all ordinary actions are available in menus.
"""
import asyncio
import html
import hashlib
import hmac
import logging
import re
import secrets
import time
from datetime import datetime, timezone

from pyrogram import filters
from pyrogram.enums import ParseMode
from pyrogram.types import InlineKeyboardButton as Button, InlineKeyboardMarkup as Keyboard
from shared_client import app
from config import FREEMIUM_LIMIT, PREMIUM_LIMIT, OWNER_ID
from utils.func import get_user_data, save_user_data, save_user_bot
from utils.custom_filters import get_user_step, set_user_step
from plugins import batch, login, localization

logger = logging.getLogger(__name__)
waiting = {}
input_languages = {}  # Snapshot for the sensitive custom-bot prompt.
busy_inputs = {}
download_quality = {}
# Private, one-use account deletion confirmations, scoped to the displayed message.
delete_confirmations = {}
DELETE_CONFIRM_SECONDS = 600
simple_confirmations = {}
SIMPLE_CONFIRM_SECONDS = 600


def credential_fingerprint(token, value):
    # A confirmation holds only a keyed digest, never a session or bot token.
    data = repr((type(value).__name__, value)).encode('utf-8')
    return hmac.new(token.encode('ascii'), data, hashlib.sha256).hexdigest()


def consume_simple_confirmation(uid, action, message_id):
    """Called only with lock_for(uid) held."""
    parts = action.split(':')
    item = simple_confirmations.get(uid)
    if (len(parts) != 3 or parts[0] != 'confirm_yes' or not item or
            item['expires'] < time.monotonic() or item['message_id'] != message_id or
            parts[1] != item['kind'] or not re.fullmatch(r'[0-9a-f]{16}', parts[2]) or
            not secrets.compare_digest(item['token'], parts[2])):
        if item and item['expires'] < time.monotonic():
            simple_confirmations.pop(uid, None)
        return None
    return simple_confirmations.pop(uid)


def markup(rows, back=None, language='en'):
    buttons = [[Button(localization.button_label(action, label, language), callback_data='ui:' + action) for label, action in row] for row in rows]
    if back:
        back_label = '🔙 वापस' if language == 'hi' else '🔙 𝗕𝗔𝗖𝗞'
        home_label = localization.button_label('home', '🏠 𝗛𝗢𝗠𝗘', language)
        buttons.append([Button(back_label, callback_data='ui:' + back), Button(home_label, callback_data='ui:home')])
    return Keyboard(buttons)


def page(title, body):
    return f'✨ <b>{title}</b> ✨\n\n<blockquote>{body}</blockquote>\n\n💙 <b>𝗣𝗼𝘄𝗲𝗿𝗲𝗱 𝗯𝘆 𝗧𝗴𝘄𝗶𝘁𝗵𝗿𝗼𝗵𝗶𝘁</b>'


PAGES = {
    'home': ('🔒 𝗦𝗔𝗩𝗘 𝗖𝗢𝗡𝗧𝗘𝗡𝗧', '👋 <b>Welcome! Choose a section below.</b>\n\n📌 <b>Use content only where you have permission.</b>', [
        [('📥 𝗦𝗔𝗩𝗘 𝗣𝗢𝗦𝗧𝗦', 'save'), ('🎬 𝗗𝗢𝗪𝗡𝗟𝗢𝗔𝗗', 'download')],
        [('⚙️ 𝗦𝗘𝗧𝗧𝗜𝗡𝗚𝗦', 'settings'), ('🔐 𝗔𝗖𝗖𝗢𝗨𝗡𝗧', 'account')],
        [('💎 𝗣𝗥𝗘𝗠𝗜𝗨𝗠', 'premium'), ('❓ 𝗛𝗘𝗟𝗣', 'help')]], None),
    'save': ('📥 𝗦𝗔𝗩𝗘 𝗣𝗢𝗦𝗧𝗦', '📌 <b>Choose one post or a batch.</b>\n🔐 <b>Configure your account and custom bot first.</b>', [
        [('1️⃣ 𝗦𝗜𝗡𝗚𝗟𝗘', 'single'), ('📚 𝗕𝗔𝗧𝗖𝗛', 'batch')],
        [('🧵 𝗧𝗢𝗣𝗜𝗖 𝗕𝗔𝗧𝗖𝗛', 'topic_batch')],
        [('📊 𝗧𝗔𝗦𝗞 𝗦𝗧𝗔𝗧𝗨𝗦', 'task'), ('🗂️ 𝗛𝗜𝗦𝗧𝗢𝗥𝗬', 'history')],
        [('🛑 𝗦𝗧𝗢𝗣 𝗧𝗔𝗦𝗞', 'stop')],
        [('⏸️ 𝗣𝗔𝗨𝗦𝗘', 'pause'), ('▶️ 𝗥𝗘𝗦𝗨𝗠𝗘', 'resume')],
        [('🪞 𝗟𝗜𝗩𝗘 𝗠𝗜𝗥𝗥𝗢𝗥', 'mirror'), ('⏹️ 𝗦𝗧𝗢𝗣 𝗠𝗜𝗥𝗥𝗢𝗥', 'mirror_stop')]], 'home'),
    'download': ('🎬 𝗗𝗢𝗪𝗡𝗟𝗢𝗔𝗗', '🌐 <b>Choose a format and then send a supported link.</b>\n⚠️ <b>Availability depends on the source.</b>', [
        [('🎥 𝗩𝗜𝗗𝗘𝗢', 'video'), ('🎵 𝗔𝗨𝗗𝗜𝗢', 'audio')]], 'home'),
    'account': ('🔐 𝗔𝗖𝗖𝗢𝗨𝗡𝗧', '👤 <b>Manage your Telegram session and custom bot.</b>\n⚠️ <b>Only connect an account to a bot operator you trust.</b>', [
        [('📊 𝗦𝗧𝗔𝗧𝗨𝗦', 'status'), ('🔑 𝗟𝗢𝗚𝗜𝗡', 'login')],
        [('🚪 𝗟𝗢𝗚𝗢𝗨𝗧', 'logout_confirm'), ('🤖 𝗖𝗨𝗦𝗧𝗢𝗠 𝗕𝗢𝗧', 'custombot')],
        [('🗑️ 𝗗𝗘𝗟𝗘𝗧𝗘 𝗗𝗔𝗧𝗔', 'delete_data_confirm')]], 'home'),
    'custombot': ('🤖 𝗖𝗨𝗦𝗧𝗢𝗠 𝗕𝗢𝗧', '🔒 <b>Your bot token grants control of your bot. Share it only with an operator you trust.</b>', [
        [('➕ 𝗔𝗗𝗗 𝗕𝗢𝗧', 'addbot'), ('🗑️ 𝗥𝗘𝗠𝗢𝗩𝗘 𝗕𝗢𝗧', 'removebot_confirm')]], 'account'),
    'settings': ('⚙️ 𝗦𝗘𝗧𝗧𝗜𝗡𝗚𝗦', '🛠️ <b>Choose what you want to customize.</b>', [
        [('👀 𝗩𝗜𝗘𝗪 𝗦𝗘𝗧𝗧𝗜𝗡𝗚𝗦', 'settings_view'), ('🧹 𝗖𝗟𝗘𝗔𝗥 𝗢𝗡𝗘', 'settings_clear')],
        [('📍 𝗗𝗘𝗦𝗧𝗜𝗡𝗔𝗧𝗜𝗢𝗡', 'destination'), ('🏷️ 𝗥𝗘𝗡𝗔𝗠𝗘 𝗧𝗔𝗚', 'rename')],
        [('💬 𝗖𝗔𝗣𝗧𝗜𝗢𝗡', 'caption'), ('🖼️ 𝗧𝗛𝗨𝗠𝗕𝗡𝗔𝗜𝗟', 'thumbnail')],
        [('🗑️ 𝗗𝗘𝗟𝗘𝗧𝗘 𝗪𝗢𝗥𝗗𝗦', 'deletewords'), ('🔄 𝗥𝗘𝗣𝗟𝗔𝗖𝗘 𝗪𝗢𝗥𝗗𝗦', 'replacewords')],
        [('⬆️ 𝗣𝗥𝗘𝗙𝗜𝗫', 'prefix'), ('⬇️ 𝗦𝗨𝗙𝗙𝗜𝗫', 'suffix')],
        [('🔽 𝗠𝗘𝗗𝗜𝗔 𝗙𝗜𝗟𝗧𝗘𝗥', 'filters'), ('💬 𝗢𝗥𝗜𝗚𝗜𝗡𝗔𝗟 𝗖𝗔𝗣𝗧𝗜𝗢𝗡', 'toggle_caption')],
        [('🌐 𝗟𝗔𝗡𝗚𝗨𝗔𝗚𝗘 / भाषा', 'language')],
        [('♻️ 𝗥𝗘𝗦𝗘𝗧', 'reset_confirm')]], 'home'),
    'premium': ('💎 𝗣𝗥𝗘𝗠𝗜𝗨𝗠', f'📚 <b>Free batch limit:</b> {FREEMIUM_LIMIT}\n🚀 <b>Premium batch limit:</b> {PREMIUM_LIMIT}\n\n⭐ <b>Choose a payment option.</b>', [
        [('📊 𝗠𝗬 𝗣𝗟𝗔𝗡', 'status'), ('⭐ 𝗧𝗘𝗟𝗘𝗚𝗥𝗔𝗠 𝗦𝗧𝗔𝗥𝗦', 'stars')],
        [('💳 𝗥𝗔𝗭𝗢𝗥𝗣𝗔𝗬', 'razorpay'), ('🎁 𝗚𝗜𝗙𝗧 𝗖𝗔𝗥𝗗', 'gift')],
        [('⏰ 𝗥𝗘𝗠𝗜𝗡𝗗𝗘𝗥𝗦', 'reminders')]], 'home'),
    'filters': ('🔽 𝗠𝗘𝗗𝗜𝗔 𝗙𝗜𝗟𝗧𝗘𝗥', 'Choose which message types to process.', [[('📑 𝗔𝗟𝗟', 'filter_all'), ('🖼️ 𝗣𝗛𝗢𝗧𝗢', 'filter_photo')], [('🎬 𝗩𝗜𝗗𝗘𝗢', 'filter_video'), ('📄 𝗗𝗢𝗖𝗨𝗠𝗘𝗡𝗧', 'filter_document')], [('🎵 𝗔𝗨𝗗𝗜𝗢', 'filter_audio'), ('📝 𝗧𝗘𝗫𝗧', 'filter_text')]], 'settings'),
    'owner': ('👑 𝗢𝗪𝗡𝗘𝗥 𝗣𝗔𝗡𝗘𝗟', '🛡️ <b>Owner-only controls. Changes are logged.</b>', [[('📊 𝗦𝗧𝗔𝗧𝗦', 'owner_stats')], [('➕ 𝗚𝗥𝗔𝗡𝗧', 'owner_grant'), ('🗑️ 𝗥𝗘𝗩𝗢𝗞𝗘', 'owner_revoke')], [('📢 𝗕𝗥𝗢𝗔𝗗𝗖𝗔𝗦𝗧', 'owner_broadcast'), ('🎁 𝗣𝗘𝗡𝗗𝗜𝗡𝗚', 'owner_gifts')], [('🧾 𝗣𝗔𝗬𝗠𝗘𝗡𝗧 𝗥𝗘𝗩𝗜𝗘𝗪', 'owner_payments')]], 'home'),
    'language': ('🌐 𝗟𝗔𝗡𝗚𝗨𝗔𝗚𝗘 / भाषा', 'Choose the main menu and input-prompt language. Other workflows may still appear in English.', [[('🇬🇧 English', 'language_en'), ('🇮🇳 हिन्दी', 'language_hi')]], 'settings'),
    'help': ('❓ 𝗛𝗘𝗟𝗣', '1️⃣ <b>Set up your account and custom bot.</b>\n2️⃣ <b>Choose Single or Batch under Save Posts.</b>\n3️⃣ <b>Send the requested link and count.</b>\n\n🔒 <b>Never share login codes, passwords, tokens or gift-card codes with an untrusted operator.</b>', [], 'home'),
}

PROMPTS = {
    'destination': ('📍 𝗗𝗘𝗦𝗧𝗜𝗡𝗔𝗧𝗜𝗢𝗡', 'Send a numeric chat ID, optionally followed by /topic ID. Example: -1001234567890/12'),
    'rename': ('🏷️ 𝗥𝗘𝗡𝗔𝗠𝗘 𝗧𝗔𝗚', 'Send the filename tag.'),
    'caption': ('💬 𝗖𝗔𝗣𝗧𝗜𝗢𝗡', 'Send the caption text.'),
    'prefix': ('⬆️ 𝗣𝗥𝗘𝗙𝗜𝗫', 'Send text to add before each message.'),
    'suffix': ('⬇️ 𝗦𝗨𝗙𝗙𝗜𝗫', 'Send text to add after each message.'),
    'thumbnail': ('🖼️ 𝗧𝗛𝗨𝗠𝗕𝗡𝗔𝗜𝗟', 'Send a photo to use as your thumbnail.'),
    'deletewords': ('🗑️ 𝗗𝗘𝗟𝗘𝗧𝗘 𝗪𝗢𝗥𝗗𝗦', 'Send words separated by spaces.'),
    'replacewords': ('🔄 𝗥𝗘𝗣𝗟𝗔𝗖𝗘 𝗪𝗢𝗥𝗗𝗦', 'Send the rule as old=new.'),
    'addbot': ('🤖 𝗔𝗗𝗗 𝗕𝗢𝗧', 'Send your custom bot token in this private chat only if you trust the operator: it grants control of your bot. The token is stored encrypted. Message deletion is best effort. Replacing it stops the live mirror. Only token format is checked, not Telegram validity. Revoke exposed tokens with BotFather.'),
    'owner_grant': ('➕ 𝗚𝗥𝗔𝗡𝗧 𝗣𝗥𝗘𝗠𝗜𝗨𝗠', 'Send user ID and number of days, for example: 123456789 30. This extends any active plan.'),
    'owner_revoke': ('🗑️ 𝗥𝗘𝗩𝗢𝗞𝗘 𝗣𝗥𝗘𝗠𝗜𝗨𝗠', 'Send the numeric user ID.'),
    'owner_broadcast': ('📢 𝗕𝗥𝗢𝗔𝗗𝗖𝗔𝗦𝗧', 'Send text to broadcast to active premium users. Please avoid sensitive information.'),
    'mirror': ('🪞 𝗟𝗜𝗩𝗘 𝗠𝗜𝗥𝗥𝗢𝗥', 'Set a destination in Settings, then send the source username or numeric chat ID. Only new messages received while the bot is online are processed.'),
    'video': ('🎥 𝗩𝗜𝗗𝗘𝗢', 'Send a supported video link.'),
    'audio': ('🎵 𝗔𝗨𝗗𝗜𝗢', 'Send a supported video link for audio.'),
}


async def render(message, name, edit=False, language='en'):
    title, body, rows, back = PAGES[name]
    if language == 'hi':
        title, body = localization.HI_PAGES.get(name, (title, body))
    if name == 'home' and message.chat.id in OWNER_ID:
        rows = rows + [[('👑 𝗢𝗪𝗡𝗘𝗥', 'owner')]]
    fn = message.edit_text if edit else message.reply_text
    await fn(page(title, body), reply_markup=markup(rows, back, language), parse_mode=ParseMode.HTML)


async def navigate(message, uid, name, edit):
    """Discard abandoned ordinary inputs, but never silently stop live work."""
    from plugins.batch_queue import lock_for
    from plugins.gift import pending as gift_pending
    from plugins.payment_review import confirmations as payment_confirmations
    async with lock_for(uid):
        delete_confirmations.pop(uid, None)
        simple_confirmations.pop(uid, None)
        if get_user_step(uid):
            language = login.login_cache.get(uid, {}).get('language', 'en')
            body = 'आगे बढ़ने से पहले लॉगिन पूरा करें या रद्द करें दबाएँ।' if language == 'hi' else 'Finish login or tap Cancel before navigating.'
            await message.reply_text(page('⚠️ लॉगिन जारी है' if language == 'hi' else '⚠️ 𝗟𝗢𝗚𝗜𝗡 𝗜𝗡 𝗣𝗥𝗢𝗚𝗥𝗘𝗦𝗦', body), reply_markup=markup([[('❌ 𝗖𝗔𝗡𝗖𝗘𝗟', 'cancel')]], 'account', language), parse_mode=ParseMode.HTML)
            return
        waiting.pop(uid, None)
        input_languages.pop(uid, None)
        gift_pending.pop(uid, None)
        payment_confirmations.pop(uid, None)
        download_quality.pop(uid, None)
        flow = batch.Z.get(uid)
        if flow and flow.get('step') in ('start', 'start_single', 'count', 'validating'):
            batch.Z.pop(uid, None)
    language = await localization.language_for(uid)
    await render(message, name, edit, language)


@app.on_message(filters.private & filters.command('start'))
async def start_menu(client, message):
    uid = message.from_user.id
    if uid in busy_inputs:
        await message.reply_text('⏳ Your previous input is still being processed. Please wait.')
        return
    await navigate(message, uid, 'home', False)


@app.on_callback_query(filters.regex(r'^ui:'))
async def menu_callback(client, query):
    if not query.message or query.message.chat.type.value != 'private':
        await query.answer('Open the bot in a private chat.', show_alert=True)
        return
    uid = query.from_user.id
    if query.message.chat.id != uid:
        await query.answer('Open the bot in your own private chat.', show_alert=True)
        return
    action = query.data[3:]
    # Navigating away invalidates a pending destructive confirmation. Serialize
    # this with confirmation creation/consumption and account credential flows.
    if not (action == 'delete_data_confirm' or action.startswith('delete_data_yes:')) and uid in delete_confirmations:
        from plugins.batch_queue import lock_for
        async with lock_for(uid):
            delete_confirmations.pop(uid, None)
    if not action.startswith('confirm_yes:') and uid in simple_confirmations:
        from plugins.batch_queue import lock_for
        async with lock_for(uid):
            simple_confirmations.pop(uid, None)
    if (action == 'owner' or action.startswith('owner_')) and uid not in OWNER_ID:
        await query.answer('Owner only.', show_alert=True)
        return
    # Leaving a payment confirmation makes its old button unusable.
    if not action.startswith(('owner_pay_confirm:', 'owner_pay_check:')):
        from plugins.payment_review import confirmations as payment_confirmations
        payment_confirmations.pop(uid, None)
    await query.answer()
    if uid in busy_inputs and action not in ('task', 'status', 'stop', 'pause', 'resume', 'history'):
        await query.message.reply_text('⏳ Your previous input is still being processed. Please wait.')
        return
    # Do not leave an invisible prompt consuming the next message after the
    # user selects a different action. Back/Home navigation explicitly abandons
    # ordinary inputs; task controls remain available during setup.
    pending_input = uid in waiting or (batch.Z.get(uid, {}).get('step') in ('start', 'start_single', 'count', 'validating'))
    if pending_input and action not in PAGES and action not in ('cancel', 'stop', 'pause', 'resume'):
        await query.message.reply_text(page('⚠️ 𝗜𝗡𝗣𝗨𝗧 𝗜𝗡 𝗣𝗥𝗢𝗚𝗥𝗘𝗦𝗦', 'Finish this input, tap Cancel, or return Home before starting another action.'), reply_markup=markup([[('❌ 𝗖𝗔𝗡𝗖𝗘𝗟', 'cancel')]], 'home'), parse_mode=ParseMode.HTML)
        return
    if action in PAGES:
        await navigate(query.message, uid, action, True)
    elif action in ('language_en', 'language_hi'):
        from plugins.batch_queue import lock_for
        from utils.func import users_collection
        language = action.partition('_')[2]
        async with lock_for(uid):
            if get_user_step(uid) or uid in batch.Z or uid in waiting or uid in busy_inputs:
                await query.message.reply_text('⚠️ Finish or cancel the current input first. / पहले इनपुट पूरा करें या रद्द करें।')
                return
            try:
                result = await users_collection.update_one({'user_id': uid}, {'$set': {'language': language}}, upsert=True)
                if not result.acknowledged or not (result.matched_count or result.upserted_id is not None):
                    raise RuntimeError('Language change not confirmed')
            except Exception:
                logger.warning('Language preference could not be saved')
                await query.message.reply_text('⚠️ Could not save language. / भाषा सेव नहीं हो पाई।')
                return
            await render(query.message, 'settings', True, language)
            await query.message.reply_text('✅ भाषा हिन्दी पर सेट है। कुछ अन्य स्क्रीन अंग्रेज़ी में रहेंगी।' if language == 'hi' else '✅ Language set to English.')
    elif action == 'video':
        from config import ENABLE_EXTERNAL_DOWNLOADS
        if not ENABLE_EXTERNAL_DOWNLOADS:
            await query.message.edit_text(page('⚠️ 𝗗𝗢𝗪𝗡𝗟𝗢𝗔𝗗', 'External downloads are disabled by the operator.'), reply_markup=markup([], 'download'), parse_mode=ParseMode.HTML)
            return
        rows = [[('✨ 𝗔𝗨𝗧𝗢', 'vq:auto')], [('📱 𝟯𝟲𝟬𝗽', 'vq:360'), ('📺 𝟰𝟴𝟬𝗽', 'vq:480')], [('📺 𝟳𝟮𝟬𝗽', 'vq:720'), ('🎬 𝟭𝟬𝟴𝟬𝗽', 'vq:1080')]]
        body = 'Choose the maximum resolution. The best available MP4 at or below that limit is used; exact resolution and availability are not guaranteed.'
        await query.message.edit_text(page('🎥 𝗩𝗜𝗗𝗘𝗢 𝗤𝗨𝗔𝗟𝗜𝗧𝗬', body), reply_markup=markup(rows, 'download'), parse_mode=ParseMode.HTML)
    elif action.startswith('vq:'):
        from config import ENABLE_EXTERNAL_DOWNLOADS
        quality = action.partition(':')[2]
        if not ENABLE_EXTERNAL_DOWNLOADS or quality not in ('auto', '360', '480', '720', '1080'):
            await query.message.edit_text(page('⚠️ 𝗗𝗢𝗪𝗡𝗟𝗢𝗔𝗗', 'Quality is invalid or external downloads are disabled.'), reply_markup=markup([], 'download'), parse_mode=ParseMode.HTML)
            return
        from plugins.batch_queue import lock_for
        async with lock_for(uid):
            language = await localization.language_for(uid)
            if get_user_step(uid) or uid in batch.Z or uid in waiting or uid in busy_inputs:
                await query.message.reply_text('⚠️ Finish or cancel the current input first.')
                return
            download_quality[uid] = quality
            waiting[uid] = 'video'
            title, prompt = localization.HI_PROMPTS['video'] if language == 'hi' else PROMPTS['video']
            label = ('ऑटो' if quality == 'auto' else f'{quality}p तक') if language == 'hi' else ('Auto' if quality == 'auto' else f'Up to {quality}p')
            try:
                await query.message.reply_text(page(title, html.escape(f'{label}. {prompt}')), reply_markup=markup([[('❌ 𝗖𝗔𝗡𝗖𝗘𝗟', 'cancel')]], 'download', language), parse_mode=ParseMode.HTML)
            except Exception:
                waiting.pop(uid, None)
                download_quality.pop(uid, None)
                raise
    elif action in PROMPTS:
        if action == 'audio':
            from config import ENABLE_EXTERNAL_DOWNLOADS
            if not ENABLE_EXTERNAL_DOWNLOADS:
                await query.message.edit_text(page('⚠️ 𝗗𝗢𝗪𝗡𝗟𝗢𝗔𝗗', 'External downloads are disabled by the operator.'), reply_markup=markup([], 'download'), parse_mode=ParseMode.HTML)
                return
        # Serialize selection with cancellation, login and other prompts.
        from plugins.batch_queue import lock_for
        async with lock_for(uid):
            language = await localization.language_for(uid)
            if get_user_step(uid) or uid in batch.Z or uid in waiting or uid in busy_inputs:
                await query.message.reply_text(page('⚠️ 𝗔𝗖𝗧𝗜𝗢𝗡 𝗜𝗡 𝗣𝗥𝗢𝗚𝗥𝗘𝗦𝗦', 'Cancel the current flow first.'), reply_markup=markup([[('❌ 𝗖𝗔𝗡𝗖𝗘𝗟', 'cancel')]], 'home'), parse_mode=ParseMode.HTML)
                return
            waiting[uid] = action
            if action == 'addbot':
                input_languages[uid] = language if language in localization.SUPPORTED else 'en'
            title, prompt = localization.HI_PROMPTS.get(action, PROMPTS[action]) if language == 'hi' else PROMPTS[action]
            try:
                await query.message.reply_text(page(title, html.escape(prompt)), reply_markup=markup([[('❌ 𝗖𝗔𝗡𝗖𝗘𝗟', 'cancel')]], 'home', language), parse_mode=ParseMode.HTML)
            except Exception:
                waiting.pop(uid, None)
                input_languages.pop(uid, None)
                raise
    elif action == 'cancel':
        from plugins.batch_queue import lock_for
        from plugins.gift import pending as gift_pending
        from plugins.payment_review import confirmations as payment_confirmations
        async with lock_for(uid):
            waiting.pop(uid, None)
            input_languages.pop(uid, None)
            gift_pending.pop(uid, None)
            payment_confirmations.pop(uid, None)
            download_quality.pop(uid, None)
            flow = batch.Z.pop(uid, None)
            if flow and flow.get('step') in ('process', 'process_single') and batch.is_user_active(uid):
                await batch.request_batch_cancel(uid)
            cache = login.login_cache.pop(uid, {})
            if cache.get('temp_client'):
                try:
                    await cache['temp_client'].disconnect()
                except Exception as exc:
                    logger.warning('Could not disconnect temporary login client: %s', type(exc).__name__)
            set_user_step(uid, None)
        language = await localization.language_for(uid)
        body = localization.translate('Input cancelled. An operation already in progress may have finished; check Task or Account Status.', language)
        await query.message.reply_text(page('✅ रद्द' if language == 'hi' else '✅ 𝗖𝗔𝗡𝗖𝗘𝗟', body), reply_markup=markup([], 'home', language), parse_mode=ParseMode.HTML)
    elif action in ('single', 'batch', 'topic_batch'):
        waiting.pop(uid, None)
        if get_user_step(uid):
            await query.message.reply_text('⚠️ Please cancel the login flow first.', reply_markup=markup([[('❌ 𝗖𝗔𝗡𝗖𝗘𝗟', 'cancel')]]))
            return
        await batch.begin_processing(client, query.message, uid, action)
    elif action == 'login':
        from plugins.batch_queue import lock_for
        async with lock_for(uid):
            language = await localization.language_for(uid)
            if batch.is_user_active(uid):
                await query.message.reply_text(localization.translate('⚠️ Stop your active task before logging in.', language))
                return
            if get_user_step(uid) or uid in login.login_cache:
                await query.message.reply_text(localization.translate('⚠️ A login is already in progress. Finish it or tap Cancel.', language), reply_markup=markup([[('❌ 𝗖𝗔𝗡𝗖𝗘𝗟', 'cancel')]], language=language))
                return
            waiting.pop(uid, None)
            if uid in batch.Z:
                await query.message.reply_text(localization.translate('⚠️ Cancel the current input first.', language), reply_markup=markup([[('❌ 𝗖𝗔𝗡𝗖𝗘𝗟', 'cancel')]], language=language))
                return
            from plugins.mirror import stop_mirror
            try:
                await stop_mirror(uid, drain=True)
            except Exception:
                logger.exception('Unable to stop mirror before login')
                await query.message.reply_text(localization.translate('⚠️ Could not stop the existing mirror. Try again later.', language))
                return
            privacy = ('लॉगिन से ऑपरेटर को आपके टेलीग्राम अकाउंट की पहुँच मिलती है। कोड और 2FA पासवर्ड केवल भरोसेमंद ऑपरेटर से साझा करें। संदेश हटाने का प्रयास किया जाता है, इसकी गारंटी नहीं है। फिर से कनेक्ट करने से पहले लाइव मिरर बंद किया जाता है।' if language == 'hi' else
                       'Login gives the operator access to your Telegram account. Share codes and 2FA passwords only if you trust the operator. Message deletion is best effort. Any live mirror is stopped before reconnecting.')
            await query.message.reply_text(page('🔐 गोपनीयता' if language == 'hi' else '🔐 𝗣𝗥𝗜𝗩𝗔𝗖𝗬', privacy), reply_markup=markup([[('❌ 𝗖𝗔𝗡𝗖𝗘𝗟', 'cancel')]], language=language), parse_mode=ParseMode.HTML)
            await login.begin_login(query.message, uid, language)
    elif action in ('logout_confirm', 'removebot_confirm', 'reset_confirm'):
        from plugins.batch_queue import lock_for
        from utils.func import users_collection
        kind = {'logout_confirm': 'logout', 'removebot_confirm': 'removebot', 'reset_confirm': 'reset'}[action]
        async with lock_for(uid):
            language = await localization.language_for(uid)
            hi = language == 'hi'
            simple_confirmations.pop(uid, None)
            token = secrets.token_hex(8)
            fingerprint = None
            if kind in ('logout', 'removebot'):
                field = 'session_string' if kind == 'logout' else 'bot_token'
                try:
                    doc = await users_collection.find_one({'user_id': uid}, {field: 1, '_id': 0}) or {}
                    fingerprint = credential_fingerprint(token, doc.get(field))
                except Exception:
                    logger.warning('Could not check stored credential for confirmation')
                    body = 'सेव जानकारी जाँची नहीं जा सकी। बाद में कोशिश करें।' if hi else 'Could not check stored credentials. Try again later.'
                    await query.message.edit_text(page('⚠️ पुष्टि' if hi else '⚠️ 𝗖𝗢𝗡𝗙𝗜𝗥𝗠', body), reply_markup=markup([], 'account', language), parse_mode=ParseMode.HTML)
                    return
            warnings = {
                'logout': ('Attempt to revoke the stored Telegram session and remove it? If revocation fails, check Telegram Settings → Devices. The live mirror is stopped.', 'सेव टेलीग्राम सेशन रद्द करके हटाने की कोशिश करें? असफल होने पर Telegram Settings → Devices जाँचें। लाइव मिरर बंद होगा।'),
                'removebot': ('Remove the stored custom bot token? Revoke it with BotFather if necessary. The live mirror is stopped.', 'सेव कस्टम बॉट टोकन हटाएँ? ज़रूरत हो तो BotFather से इसे रद्द करें। लाइव मिरर बंद होगा।'),
                'reset': ('Reset current content settings and thumbnail? This may affect an active task or mirror; account and billing records remain.', 'मौजूदा कंटेंट सेटिंग्स और थंबनेल रीसेट करें? इससे चालू टास्क या मिरर प्रभावित हो सकता है; अकाउंट और बिलिंग रिकॉर्ड बने रहेंगे।'),
            }
            body = warnings[kind][1 if hi else 0] + (' पुष्टि 10 मिनट में समाप्त होगी।' if hi else ' Confirmation expires in 10 minutes.')
            simple_confirmations[uid] = {'kind': kind, 'token': token, 'message_id': query.message.id,
                                         'expires': time.monotonic() + SIMPLE_CONFIRM_SECONDS, 'fingerprint': fingerprint}
            try:
                await query.message.edit_text(page('⚠️ पुष्टि करें' if hi else '⚠️ 𝗖𝗢𝗡𝗙𝗜𝗥𝗠', body),
                    reply_markup=markup([[('✅ 𝗬𝗘𝗦', 'confirm_yes:' + kind + ':' + token), ('❌ 𝗡𝗢', 'home')]], language=language), parse_mode=ParseMode.HTML)
            except Exception:
                simple_confirmations.pop(uid, None)
                raise
    elif action in ('logout', 'removebot', 'reset') or action.startswith('confirm_yes:'):
        from plugins.batch_queue import lock_for
        from utils.func import users_collection
        import os
        async with lock_for(uid):
            language = await localization.language_for(uid)
            hi = language == 'hi'
            item = consume_simple_confirmation(uid, action, getattr(query.message, 'id', None))
            if not item or item['kind'] not in ('logout', 'removebot', 'reset'):
                body = 'पुष्टि समाप्त या अमान्य है। मेनू से दोबारा अनुरोध करें।' if hi else 'Confirmation expired or invalid. Open the menu and request it again.'
                await query.message.edit_text(page('⚠️ पुष्टि' if hi else '⚠️ 𝗖𝗢𝗡𝗙𝗜𝗥𝗠', body), reply_markup=markup([], 'account', language), parse_mode=ParseMode.HTML)
                return
            kind = item['kind']
            if kind in ('logout', 'removebot') and (batch.is_user_active(uid) or get_user_step(uid)):
                body = ('पहले चालू टास्क रोकें या लॉगिन पूरा करें।' if hi else
                        'Stop your active task or finish login before continuing.')
                await query.message.reply_text('⚠️ ' + body)
                return
            if kind == 'reset':
                try:
                    await users_collection.update_one({'user_id': uid}, {'$unset': {k: '' for k in ('chat_id', 'rename_tag', 'caption', 'delete_words', 'replacement_words', 'prefix', 'suffix', 'media_filter', 'remove_caption')}})
                    try: os.remove(f'{uid}.jpg')
                    except FileNotFoundError: pass
                except Exception:
                    logger.warning('Content reset incomplete or uncertain')
                    body = ('रीसेट पूरा होने की पुष्टि नहीं हुई; कुछ सेटिंग्स हट चुकी हो सकती हैं। जाँचकर फिर कोशिश करें।' if hi else
                            'Could not confirm reset completed; some settings may already be cleared. Check them before retrying.')
                    await query.message.edit_text(page('⚠️ रीसेट' if hi else '⚠️ 𝗥𝗘𝗦𝗘𝗧', body), reply_markup=markup([], 'settings', language), parse_mode=ParseMode.HTML)
                    return
                body = 'कंटेंट सेटिंग्स और थंबनेल रीसेट हुए। अकाउंट और बिलिंग रिकॉर्ड नहीं बदले।' if hi else 'Content settings and thumbnail were reset. Account and billing records were not changed.'
                await query.message.edit_text(page('✅ रीसेट' if hi else '✅ 𝗥𝗘𝗦𝗘𝗧', body), reply_markup=markup([], 'settings', language), parse_mode=ParseMode.HTML)
                return
            field = 'session_string' if kind == 'logout' else 'bot_token'
            try:
                doc = await users_collection.find_one({'user_id': uid}, {field: 1, '_id': 0}) or {}
                value = doc.get(field)
                if not hmac.compare_digest(item['fingerprint'], credential_fingerprint(item['token'], value)):
                    body = 'सेव जानकारी बदल गई है। मेनू से दोबारा पुष्टि करें।' if hi else 'Stored credential changed. Open the menu and confirm again.'
                    await query.message.edit_text(page('⚠️ पुष्टि' if hi else '⚠️ 𝗖𝗢𝗡𝗙𝗜𝗥𝗠', body), reply_markup=markup([], 'account', language), parse_mode=ParseMode.HTML)
                    return
            except Exception:
                logger.warning('Could not read credential before removal')
                body = 'सेव जानकारी जाँची नहीं जा सकी। बाद में फिर कोशिश करें।' if hi else 'Could not check stored credentials. Try again later.'
                await query.message.edit_text(page('⚠️ अकाउंट' if hi else '⚠️ 𝗔𝗖𝗖𝗢𝗨𝗡𝗧', body), reply_markup=markup([], 'account', language), parse_mode=ParseMode.HTML)
                return
            from plugins.mirror import stop_mirror
            try:
                await stop_mirror(uid, drain=True)
            except Exception:
                logger.warning('Could not stop mirror for credential removal')
                body = 'मिरर बंद होने की पुष्टि नहीं हुई। फिर कोशिश करें।' if hi else 'Could not confirm mirror stopped. Try again.'
                await query.message.edit_text(page('⚠️ अकाउंट' if hi else '⚠️ 𝗔𝗖𝗖𝗢𝗨𝗡𝗧', body), reply_markup=markup([], 'account', language), parse_mode=ParseMode.HTML)
                return
            if kind == 'logout':
                from utils.encrypt import dcs
                from pyrogram import Client
                from config import API_ID, API_HASH
                outcome = 'No stored session found.'
                if value:
                    active = batch.UC.pop(uid, None)
                    if active:
                        try: await active.stop()
                        except Exception: logger.warning('Unable to stop client during logout')
                    temp = None
                    try:
                        temp = Client(f'logout_{uid}', api_id=API_ID, api_hash=API_HASH, session_string=dcs(value), in_memory=True)
                        await temp.connect()
                        await temp.log_out()
                        outcome = 'Telegram session revoked and removed.'
                    except Exception:
                        logger.warning('Could not revoke Telegram session')
                        outcome = 'Stored session removed, but Telegram revocation failed. Revoke it in Telegram Settings → Devices.'
                        if temp:
                            try: await temp.disconnect()
                            except Exception: pass
                    try:
                        result = await users_collection.update_one({'user_id': uid, field: value}, {'$unset': {field: ''}})
                        if not result.modified_count:
                            outcome = '⚠️ Could not confirm removal from storage. Check Telegram Settings → Devices and contact the operator.'
                    except Exception:
                        logger.warning('Could not confirm session removal')
                        outcome = '⚠️ Could not confirm removal from storage. Check Telegram Settings → Devices and contact the operator.'
                await query.message.edit_text(page('🚪 लॉगआउट' if hi else '🚪 𝗟𝗢𝗚𝗢𝗨𝗧', html.escape(localization.translate(outcome, language))), reply_markup=markup([], 'account', language), parse_mode=ParseMode.HTML)
            else:
                active = batch.UB.pop(uid, None)
                if active:
                    try: await active.stop()
                    except Exception: logger.warning('Could not stop custom bot')
                try:
                    if value is not None:
                        result = await users_collection.update_one({'user_id': uid, field: value}, {'$unset': {field: ''}})
                        if not result.modified_count:
                            raise RuntimeError('Credential changed or removal unconfirmed')
                    for path in (f'user_{uid}.session', f'user_{uid}.session-journal'):
                        try: os.remove(path)
                        except FileNotFoundError: pass
                    body = ('Custom bot removed. Revoke its token with BotFather if necessary.' if value else 'No stored custom bot token found. Revoke any unwanted token with BotFather.')
                except Exception:
                    logger.warning('Could not confirm custom bot removal')
                    body = '⚠️ Could not confirm token and local session removal. Check storage, retry, and revoke the token with BotFather.'
                await query.message.edit_text(page('🤖 कस्टम बॉट' if hi else '🤖 𝗖𝗨𝗦𝗧𝗢𝗠 𝗕𝗢𝗧', localization.translate(body, language)), reply_markup=markup([], 'custombot', language), parse_mode=ParseMode.HTML)
    elif action == 'settings_clear':
        from plugins.settings_clear import show_choices
        language = await localization.language_for(uid)
        await show_choices(query.message, uid, language)
    elif action.startswith('setting_clear_yes:'):
        from plugins.settings_clear import clear
        try:
            ok, reason = await clear(uid, action.partition(':')[2])
        except Exception:
            logger.exception('Could not confirm settings deletion')
            ok, reason = False, 'Could not confirm the change. Check your settings before retrying.'
        language = await localization.language_for(uid)
        await query.message.edit_text(page('🧹 सेटिंग्स' if language == 'hi' else '🧹 𝗦𝗘𝗧𝗧𝗜𝗡𝗚𝗦', html.escape(localization.translate(reason, language))), reply_markup=markup([], 'settings_clear', language), parse_mode=ParseMode.HTML)
    elif action.startswith('setting_clear:'):
        from plugins.settings_clear import ask_clear
        try:
            language = await localization.language_for(uid)
            await ask_clear(query.message, uid, action.partition(':')[2], language)
        except Exception:
            logger.exception('Could not load setting for deletion')
            await query.message.edit_text(page('⚠️ 𝗦𝗘𝗧𝗧𝗜𝗡𝗚𝗦', 'Could not load the setting. Try again later.'), reply_markup=markup([], 'settings'), parse_mode=ParseMode.HTML)
    elif action == 'settings_view':
        from plugins.settings_view import show_settings
        try:
            await show_settings(query.message, uid)
        except Exception:
            logger.exception('Could not load content settings')
            await query.message.edit_text(page('⚠️ 𝗦𝗘𝗧𝗧𝗜𝗡𝗚𝗦', 'Could not load settings. Try again later.'), reply_markup=markup([], 'settings'), parse_mode=ParseMode.HTML)
    elif action.startswith('filter_'):
        selected = action[7:]
        if selected not in {'all', 'photo', 'video', 'document', 'audio', 'text'}:
            return
        await save_user_data(uid, 'media_filter', selected)
        await query.message.edit_text(page('🔽 𝗙𝗜𝗟𝗧𝗘𝗥', f'✅ Selected: <b>{html.escape(selected)}</b>'), reply_markup=markup([], 'filters'), parse_mode=ParseMode.HTML)
    elif action == 'toggle_caption':
        data = await get_user_data(uid) or {}
        remove = not data.get('remove_caption', False)
        await save_user_data(uid, 'remove_caption', remove)
        await query.message.edit_text(page('💬 𝗖𝗔𝗣𝗧𝗜𝗢𝗡', 'Original media caption will be removed.' if remove else 'Original media caption will be kept.'), reply_markup=markup([], 'settings'), parse_mode=ParseMode.HTML)
    elif action == 'mirror_stop':
        from plugins.mirror import stop_mirror
        stopped = await stop_mirror(uid)
        await query.message.edit_text(page('🪞 𝗠𝗜𝗥𝗥𝗢𝗥', 'Mirror stopped. An in-flight message may still finish.' if stopped else 'No active mirror.'), reply_markup=markup([], 'save'), parse_mode=ParseMode.HTML)
    elif action == 'status':
        # Distinguish a missing record from a failed read, and never render secrets.
        from utils.func import users_collection, premium_users_collection
        language = await localization.language_for(uid)
        hi = language == 'hi'
        unknown = 'अज्ञात' if hi else 'Unknown'
        try:
            data = await users_collection.find_one({'user_id': uid}, {'session_string': 1, 'bot_token': 1, '_id': 0}) or {}
            session_state = ('सेव है' if hi else 'Saved') if data.get('session_string') else ('सेव नहीं है' if hi else 'Not saved')
            bot_state = ('सेव है' if hi else 'Saved') if data.get('bot_token') else ('सेव नहीं है' if hi else 'Not saved')
        except Exception:
            logger.warning('Could not load account status')
            session_state = bot_state = unknown
        if uid in OWNER_ID:
            expiry = 'ओनर' if hi else 'Owner'
        else:
            try:
                plan = await premium_users_collection.find_one({'user_id': uid}, {'subscription_end': 1, '_id': 0})
                if plan is None:
                    expiry = 'फ़्री' if hi else 'Free'
                elif isinstance(plan.get('subscription_end'), datetime):
                    end = plan['subscription_end']
                    # Mongo dates are normally naive UTC; normalize aware dates.
                    if end.tzinfo is not None and end.utcoffset() is not None:
                        end = end.astimezone(timezone.utc).replace(tzinfo=None)
                    active = end > datetime.utcnow()
                    prefix = ('सक्रिय, समाप्ति: ' if active else 'समाप्त हुआ: ') if hi else ('Active until ' if active else 'Expired on ')
                    expiry = prefix + end.strftime('%d %b %Y %H:%M UTC')
                else:
                    expiry = unknown
            except Exception:
                logger.warning('Could not load plan status')
                expiry = unknown
        if hi:
            body = (f'🔐 <b>सेशन:</b> {session_state}\n🤖 <b>कस्टम बॉट:</b> {bot_state}\n💎 <b>प्लान:</b> {html.escape(expiry)}'
                    '\n\nℹ️ सेव होने का मतलब यह नहीं कि सेशन या टोकन अभी भी मान्य है।')
        else:
            body = (f'🔐 <b>Session:</b> {session_state}\n🤖 <b>Custom bot:</b> {bot_state}\n💎 <b>Plan:</b> {html.escape(expiry)}'
                    '\n\nℹ️ Saved does not verify that a session or token is still valid.')
        await query.message.edit_text(page('📊 स्थिति' if hi else '📊 𝗦𝗧𝗔𝗧𝗨𝗦', body), reply_markup=markup([], 'account', language), parse_mode=ParseMode.HTML)
    elif action in ('reminders', 'reminders_on', 'reminders_off'):
        from plugins import reminders
        from plugins.batch_queue import lock_for
        async with lock_for(uid):
            available = reminders.enabled()
            if action == 'reminders_on' and available:
                await save_user_data(uid, 'expiry_reminders', True)
            elif action == 'reminders_off':
                await save_user_data(uid, 'expiry_reminders', False)
            data = await get_user_data(uid) or {}
            active = data.get('expiry_reminders') is True
            state = 'ON' if active else 'OFF'
            body = (f'⏰ <b>Your reminders:</b> {state}\n'
                    'Get one best-effort reminder before your premium expires. '
                    'Delivery is not guaranteed.') if available else (
                    f'⏰ <b>Your saved preference:</b> {state}\n'
                    'Reminders are currently disabled by the operator.')
            rows = [[('🔔 𝗧𝗨𝗥𝗡 𝗢𝗡', 'reminders_on')]] if available and not active else []
            if active:
                rows.append([('🔕 𝗧𝗨𝗥𝗡 𝗢𝗙𝗙', 'reminders_off')])
            await query.message.edit_text(page('⏰ 𝗥𝗘𝗠𝗜𝗡𝗗𝗘𝗥𝗦', body), reply_markup=markup(rows, 'premium'), parse_mode=ParseMode.HTML)
    elif action == 'history' or action.startswith('history_page:'):
        from plugins.batch_history import show_history, cancel_confirmation
        await cancel_confirmation(uid)
        number = action.partition(':')[2] if ':' in action else '0'
        if not number.isascii() or not number.isdigit() or len(number) > 3:
            number = '-1'
        try:
            language = await localization.language_for(uid)
            await show_history(query.message, uid, int(number), language)
        except Exception:
            logger.exception('Cannot load batch history')
            await query.message.edit_text(page('⚠️ 𝗛𝗜𝗦𝗧𝗢𝗥𝗬', 'Could not load history. Try again later.'), reply_markup=markup([], 'save'), parse_mode=ParseMode.HTML)
    elif action.startswith('history_view:'):
        from plugins.batch_history import show_detail
        try:
            language = await localization.language_for(uid)
            await show_detail(query.message, uid, action.partition(':')[2], language)
        except Exception:
            logger.exception('Cannot load batch history details')
            await query.message.edit_text(page('⚠️ 𝗛𝗜𝗦𝗧𝗢𝗥𝗬', 'Could not load details. Try again later.'), reply_markup=markup([], 'history'), parse_mode=ParseMode.HTML)
    elif action == 'history_clear':
        from plugins.batch_history import ask_clear
        language = await localization.language_for(uid)
        await ask_clear(query.message, uid, language)
    elif action.startswith('history_clear_yes:'):
        from plugins.batch_history import clear_history
        try:
            ok, reason = await clear_history(uid, action.partition(':')[2])
        except Exception:
            logger.exception('Batch history deletion failed or is uncertain')
            ok, reason = False, 'Deletion could not be confirmed; check history before trying again.'
        language = await localization.language_for(uid)
        await query.message.edit_text(page('🗑️ इतिहास' if language == 'hi' else '🗑️ 𝗛𝗜𝗦𝗧𝗢𝗥𝗬', html.escape(localization.translate(reason, language))), reply_markup=markup([], 'history', language), parse_mode=ParseMode.HTML)
    elif action == 'task':
        from plugins.batch_queue import get_job, summary
        language = await localization.language_for(uid)
        active_info = batch.get_batch_info(uid) or {}
        if active_info.get('kind') == 'single':
            body = ('एक पोस्ट प्रोसेस हो रही है। संभव होने पर भेजने से पहले रुक जाएगी; भेजना शुरू हो चुका हो तो पूरा हो सकता है।' if language == 'hi' else 'A single post is processing. Stop takes effect before delivery when possible; an in-flight send may still finish.')
            await query.message.edit_text(page('📊 टास्क' if language == 'hi' else '📊 𝗧𝗔𝗦𝗞', body), reply_markup=markup([[('🛑 𝗦𝗧𝗢𝗣', 'stop')], [('🗂️ 𝗛𝗜𝗦𝗧𝗢𝗥𝗬', 'history')]], 'save', language), parse_mode=ParseMode.HTML)
            return
        job = await get_job(uid)
        if not job:
            await query.message.edit_text(page('📊 टास्क' if language == 'hi' else '📊 𝗧𝗔𝗦𝗞', 'कोई सेव बैच नहीं है।' if language == 'hi' else 'No saved batch.'), reply_markup=markup([], 'save', language), parse_mode=ParseMode.HTML)
            return
        state = html.escape(localization.status_label(job.get('status', 'unknown'), language))
        body = f"📌 <b>{'स्थिति' if language == 'hi' else 'Status'}:</b> {state}\n{html.escape(summary(job, language))}"
        if job.get('source_topic') is not None:
            body += f"\n🧵 <b>{'स्रोत टॉपिक' if language == 'hi' else 'Source topic'}:</b> {html.escape(str(job['source_topic']))} · {'ID स्कैन की गईं' if language == 'hi' else 'IDs scanned'}"
        active = batch.is_user_active(uid)
        if job.get('inflight') is not None:
            if language == 'hi':
                body += ('\n⏳ <b>मौजूदा आइटम प्रोसेस हो रहा है।</b>' if active else
                         '\n⚠️ <b>एक आइटम की डिलीवरी का परिणाम अज्ञात है।</b>')
            else:
                body += ('\n⏳ <b>Current item is being processed.</b>' if active else
                         '\n⚠️ <b>One item has an unknown delivery result.</b>')
        status = job.get('status')
        rows = []
        if status == 'running' and active:
            rows.append([('⏸️ 𝗣𝗔𝗨𝗦𝗘', 'pause')])
        if ((active and status == 'paused') or (not active and status in ('paused', 'interrupted', 'cancelled', 'running', 'cancelling') and job.get('pending'))):
            rows.append([('▶️ 𝗥𝗘𝗦𝗨𝗠𝗘', 'resume')])
        if status in ('running', 'paused', 'interrupted', 'cancelling'):
            rows.append([('🛑 𝗦𝗧𝗢𝗣', 'stop')])
        if status in ('completed', 'cancelled') and job.get('failed_ids') and not job.get('pending') and job.get('inflight') is None:
            rows.append([('🔁 𝗥𝗘𝗧𝗥𝗬 𝗙𝗔𝗜𝗟𝗘𝗗', 'retry_failed')])
        rows.append([('🗂️ 𝗛𝗜𝗦𝗧𝗢𝗥𝗬', 'history')])
        await query.message.edit_text(page('📊 टास्क' if language == 'hi' else '📊 𝗧𝗔𝗦𝗞', body), reply_markup=markup(rows, 'save', language), parse_mode=ParseMode.HTML)
    elif action == 'pause':
        ok = await batch.set_batch_paused(uid, True)
        body = 'Pause requested; the current file may finish first.' if ok else 'No active batch.'
        language = await localization.language_for(uid)
        await query.message.edit_text(page('⏸️ टास्क' if language == 'hi' else '⏸️ 𝗧𝗔𝗦𝗞', localization.translate(body, language)), reply_markup=markup([], 'save', language), parse_mode=ParseMode.HTML)
    elif action == 'resume' or action.startswith('ri:'):
        from plugins.batch_queue import resume, get_job, MAX_MESSAGE_ID
        language = await localization.language_for(uid)
        choice = None
        expected_job_id = expected_inflight = expected_nonce = None
        if action != 'resume':
            match = re.fullmatch(r'ri:(s|r):([0-9a-f]{24}):([1-9][0-9]{0,9}):([0-9a-f]{16}|legacy)', action)
            if not match or int(match.group(3)) > MAX_MESSAGE_ID:
                await query.message.edit_text(page('⚠️ 𝗧𝗔𝗦𝗞', localization.translate('This choice is invalid or outdated. Open Task Status again.', language)), reply_markup=markup([], 'task', language), parse_mode=ParseMode.HTML)
                return
            choice_key, expected_job_id, item, expected_nonce = match.groups()
            choice = 'skip' if choice_key == 's' else 'retry'
            expected_inflight = int(item)
        if action == 'resume' and batch.is_user_active(uid):
            info = batch.get_batch_info(uid) or {}
            ok = await batch.set_batch_paused(uid, False) if info.get('paused') else False
            reason = 'Batch resumed.' if ok else 'Batch is already running or stopping.'
        else:
            ok, reason = await resume(uid, query.message, choice, expected_job_id, expected_inflight, expected_nonce)
        if reason == 'uncertain':
            job = await get_job(uid)
            job_id = job.get('job_id', '') if job else ''
            item = job.get('inflight') if job else None
            nonce = (job.get('inflight_nonce') or 'legacy') if job else ''
            if not isinstance(nonce, str) or not re.fullmatch(r'[0-9a-f]{16}|legacy', nonce):
                await query.message.edit_text(page('⚠️ 𝗧𝗔𝗦𝗞', localization.translate('Task checkpoint is invalid. Contact support.', language)), reply_markup=markup([], 'task', language), parse_mode=ParseMode.HTML)
                return
            if not isinstance(job_id, str) or not re.fullmatch(r'[0-9a-f]{24}', job_id) or type(item) is not int or not 1 <= item <= MAX_MESSAGE_ID:
                await query.message.edit_text(page('⚠️ 𝗧𝗔𝗦𝗞', localization.translate('Task changed or the item is invalid. Open Task Status again.', language)), reply_markup=markup([], 'task', language), parse_mode=ParseMode.HTML)
                return
            body = ((f'⚠️ संदेश ID <code>{item}</code> रुकावट से पहले भेजा जा चुका हो सकता है। '
                     'डुप्लिकेट से बचने के लिए छोड़ें चुनें, या उसे दोबारा भेजने के लिए फिर भेजें चुनें। छोड़ने पर संदेश छूट सकता है।') if language == 'hi' else
                    (f'⚠️ Message ID <code>{item}</code> may already have been delivered before the interruption. '
                     'Choose Skip to avoid a possible duplicate, or Retry to send it again. Skipping may omit the message.'))
            rows = [[('⏭️ 𝗦𝗞𝗜𝗣 𝗜𝗧𝗘𝗠', f'ri:s:{job_id}:{item}:{nonce}')], [('🔁 𝗥𝗘𝗧𝗥𝗬 𝗜𝗧𝗘𝗠', f'ri:r:{job_id}:{item}:{nonce}')]]
            await query.message.edit_text(page('❔ अनिश्चित आइटम' if language == 'hi' else '❔ 𝗨𝗡𝗖𝗘𝗥𝗧𝗔𝗜𝗡 𝗜𝗧𝗘𝗠', body), reply_markup=markup(rows, 'save', language), parse_mode=ParseMode.HTML)
        else:
            if ok:
                batch.Z.pop(uid, None)
            await query.message.edit_text(page('▶️ 𝗧𝗔𝗦𝗞', html.escape(localization.translate(reason, language))), reply_markup=markup([], 'save', language), parse_mode=ParseMode.HTML)
    elif action == 'retry_failed':
        from plugins.batch_queue import get_job, failed_fingerprint
        language = await localization.language_for(uid)
        job = await get_job(uid)
        job_id = job.get('job_id', '') if job else ''
        failed = job.get('failed_ids', []) if job else []
        if not isinstance(job_id, str) or not re.fullmatch(r'[0-9a-f]{24}', job_id) or not failed:
            await query.message.edit_text(page('🔁 𝗥𝗘𝗧𝗥𝗬', localization.translate('No failed items to retry.', language)), reply_markup=markup([], 'task', language), parse_mode=ParseMode.HTML)
            return
        if job.get('pending') or job.get('inflight') is not None:
            await query.message.edit_text(page('🔁 𝗥𝗘𝗧𝗥𝗬', localization.translate('Resume the remaining items before retrying failures.', language)), reply_markup=markup([], 'task', language), parse_mode=ParseMode.HTML)
            return
        fingerprint = failed_fingerprint(failed)
        body = ((f'⚠️ {len(failed)} असफल आइटम फिर चलाएँ? त्रुटि से पहले कुछ भेजे जा चुके हो सकते हैं। '
                 'फिर कोशिश करने पर डुप्लिकेट बन सकते हैं।') if language == 'hi' else
                (f'⚠️ Retry {len(failed)} failed items? Some may already have been delivered before an error. '
                 'Retrying could create duplicates.'))
        await query.message.edit_text(page('⚠️ असफल फिर चलाएँ' if language == 'hi' else '⚠️ 𝗥𝗘𝗧𝗥𝗬 𝗙𝗔𝗜𝗟𝗘𝗗', body), reply_markup=markup([[('✅ 𝗥𝗘𝗧𝗥𝗬', f'rf:{job_id}:{fingerprint}'), ('❌ रद्द करें' if language == 'hi' else '❌ 𝗖𝗔𝗡𝗖𝗘𝗟', 'task')]], 'save', language), parse_mode=ParseMode.HTML)
    elif action.startswith('rf:'):
        from plugins.batch_queue import retry_failed
        language = await localization.language_for(uid)
        match = re.fullmatch(r'rf:([0-9a-f]{24}):([0-9a-f]{16})', action)
        if not match:
            ok, reason = False, 'This confirmation is invalid. Open Task Status again.'
        else:
            ok, reason = await retry_failed(uid, query.message, *match.groups())
        if ok:
            batch.Z.pop(uid, None)
        await query.message.edit_text(page('🔁 𝗥𝗘𝗧𝗥𝗬', html.escape(localization.translate(reason, language))), reply_markup=markup([], 'save', language), parse_mode=ParseMode.HTML)
    elif action in ('retry_failed_confirm', 'resume_skip_inflight', 'resume_retry_inflight') or action.startswith('resume_item:'):
        language = await localization.language_for(uid)
        await query.message.edit_text(page('⚠️ 𝗧𝗔𝗦𝗞', localization.translate('This old button has expired. Open Task Status and review the current batch.', language)), reply_markup=markup([], 'task', language), parse_mode=ParseMode.HTML)
    elif action == 'stop':
        from plugins.batch_queue import cancel_saved
        stopped = await batch.request_batch_cancel(uid) if batch.is_user_active(uid) else await cancel_saved(uid)
        language = await localization.language_for(uid)
        body = 'Stop requested; the current file may finish first.' if stopped else 'No active or interrupted batch.'
        await query.message.edit_text(page('🛑 टास्क' if language == 'hi' else '🛑 𝗧𝗔𝗦𝗞', localization.translate(body, language)), reply_markup=markup([], 'save', language), parse_mode=ParseMode.HTML)
    elif action == 'stars':
        from plugins.pay import show_plans
        await show_plans(query.message)
    elif action == 'razorpay':
        from plugins.razorpay import show_razorpay
        await show_razorpay(query.message)
    elif action == 'gift':
        from plugins.gift import show_gift
        await show_gift(query.message)
    elif action == 'owner_gifts':
        from plugins.gift import show_pending
        await show_pending(query.message)
    elif action == 'owner_payments' or action.startswith(('owner_pay_list:', 'owner_pay_view:', 'owner_pay_check:', 'owner_pay_confirm:')):
        from plugins import payment_review
        try:
            parts = action.split(':')
            if action == 'owner_payments':
                payment_review.confirmations.pop(uid, None)
                await payment_review.show_dashboard(query.message)
            elif parts[0] == 'owner_pay_list' and len(parts) == 3 and parts[2].isdigit():
                await payment_review.show_list(query.message, parts[1], int(parts[2]))
            elif parts[0] in ('owner_pay_view', 'owner_pay_check') and len(parts) == 3:
                identifier = payment_review.full_id(parts[1], parts[2])
                if not identifier:
                    raise ValueError('Invalid invoice reference')
                if parts[0] == 'owner_pay_view':
                    await payment_review.show_detail(query.message, parts[1], identifier)
                else:
                    await payment_review.ask_complete(query.message, uid, parts[1], identifier)
            elif parts[0] == 'owner_pay_confirm' and len(parts) == 2 and re.fullmatch(r'[a-f0-9]{16}', parts[1]):
                ok, reason = await payment_review.complete(uid, parts[1])
                await query.message.edit_text(page('✅ 𝗥𝗘𝗩𝗜𝗘𝗪' if ok else '⚠️ 𝗥𝗘𝗩𝗜𝗘𝗪', html.escape(reason)), reply_markup=markup([], 'owner_payments'), parse_mode=ParseMode.HTML)
            else:
                raise ValueError('Invalid review action')
        except Exception:
            logger.exception('Payment review failed')
            await query.message.edit_text(page('⚠️ 𝗥𝗘𝗩𝗜𝗘𝗪', 'Could not confirm this action. Check the record and audit log before retrying.'), reply_markup=markup([], 'owner_payments'), parse_mode=ParseMode.HTML)
    elif action == 'owner_stats':
        from utils.func import users_collection, premium_users_collection
        users = await users_collection.count_documents({})
        premium = await premium_users_collection.count_documents({'subscription_end': {'$gt': datetime.utcnow()}})
        body = f'👤 <b>Stored users:</b> {users}\n💎 <b>Active premium:</b> {premium}\n📚 <b>Active batches:</b> {len(batch.ACTIVE_USERS)}'
        await query.message.edit_text(page('📊 𝗦𝗧𝗔𝗧𝗦', body), reply_markup=markup([], 'owner'), parse_mode=ParseMode.HTML)
    elif action == 'delete_data_confirm':
        from plugins.batch_queue import lock_for
        async with lock_for(uid):
            language = await localization.language_for(uid)
            token = secrets.token_hex(8)
            delete_confirmations[uid] = (token, time.monotonic() + DELETE_CONFIRM_SECONDS, query.message.id)
            body = (('अकाउंट सेटिंग्स, सेव credentials, मिरर, सेव टास्क और इतिहास हटाएँ? सेशन रद्द करने का प्रयास होगा; '
                     'बिलिंग रिकॉर्ड और बैकअप रखे जा सकते हैं। पुष्टि 10 मिनट में समाप्त होगी।') if language == 'hi' else
                    ('Delete account settings, stored credentials, mirror, saved jobs and history? Session revocation is best effort. '
                     'Billing records and backups may be retained. Confirmation expires in 10 minutes.'))
            try:
                await query.message.edit_text(page('⚠️ पुष्टि करें' if language == 'hi' else '⚠️ 𝗖𝗢𝗡𝗙𝗜𝗥𝗠', body),
                    reply_markup=markup([[('✅ 𝗬𝗘𝗦', 'delete_data_yes:' + token), ('❌ 𝗡𝗢', 'home')]], language=language), parse_mode=ParseMode.HTML)
            except Exception:
                delete_confirmations.pop(uid, None)
                raise
    elif action == 'delete_data' or action.startswith('delete_data_yes:'):
        from plugins.batch_queue import lock_for
        async with lock_for(uid):
            import os
            from plugins.mirror import stop_mirror, mirrors
            from plugins.batch_queue import jobs, history
            from utils.func import users_collection
            language = await localization.language_for(uid)
            hi = language == 'hi'
            token = action.partition(':')[2]
            confirmation = delete_confirmations.get(uid)
            valid = (action.startswith('delete_data_yes:') and confirmation and
                     confirmation[1] >= time.monotonic() and confirmation[2] == query.message.id and
                     bool(re.fullmatch(r'[0-9a-f]{16}', token)) and secrets.compare_digest(confirmation[0], token))
            if not valid:
                if confirmation and confirmation[1] < time.monotonic():
                    delete_confirmations.pop(uid, None)
                body = 'पुष्टि समाप्त या अमान्य है। अकाउंट से डेटा हटाने का अनुरोध फिर खोलें।' if hi else 'Confirmation expired or invalid. Open Delete Data from Account again.'
                await query.message.edit_text(page('⚠️ पुष्टि' if hi else '⚠️ 𝗖𝗢𝗡𝗙𝗜𝗥𝗠', body), reply_markup=markup([], 'account', language), parse_mode=ParseMode.HTML)
                return
            # Consume before any effects; repeated taps or later data are protected.
            delete_confirmations.pop(uid, None)
            if batch.is_user_active(uid) or get_user_step(uid):
                body = 'डेटा हटाने से पहले चालू टास्क रोकें या लॉगिन पूरा करें।' if hi else 'Stop your active task or finish login before deleting data.'
                await query.message.edit_text(page('⚠️ पहले रोकें' if hi else '⚠️ 𝗦𝗧𝗢𝗣 𝗙𝗜𝗥𝗦𝗧', body), reply_markup=markup([], 'account', language), parse_mode=ParseMode.HTML)
                return
            # Read directly so a failed read cannot be mistaken for no session.
            try:
                data = await users_collection.find_one({'user_id': uid}, {'session_string': 1, '_id': 0}) or {}
            except Exception:
                logger.warning('Could not read session before account deletion')
                body = 'सेशन जाँच नहीं सका। कोई डेटा नहीं हटाया गया; बाद में फिर कोशिश करें।' if hi else 'Could not check the session. No data was deleted; try again later.'
                await query.message.edit_text(page('⚠️ डेटा हटाना' if hi else '⚠️ 𝗗𝗘𝗟𝗘𝗧𝗘 𝗗𝗔𝗧𝗔', body), reply_markup=markup([], 'account', language), parse_mode=ParseMode.HTML)
                return
            try:
                batch.Z.pop(uid, None)
                download_quality.pop(uid, None)
                from plugins.batch_history import confirmations as history_confirmations
                from plugins.settings_clear import confirmations as setting_confirmations
                from plugins.gift import pending as gift_pending
                gift_pending.pop(uid, None)
                history_confirmations.pop(uid, None)
                setting_confirmations.pop(uid, None)
                await stop_mirror(uid, drain=True)
                await mirrors.delete_one({'user_id': uid})
                await jobs.delete_one({'_id': uid})
                await history.delete_many({'user_id': uid})
                from utils.encrypt import dcs
                from pyrogram import Client
                from config import API_ID, API_HASH
                session = data.get('session_string')
                revoked = False
                active = batch.UC.pop(uid, None)
                if active:
                    try: await active.stop()
                    except Exception: logger.warning('Client stop failed during deletion')
                bot = batch.UB.pop(uid, None)
                if bot:
                    try: await bot.stop()
                    except Exception: logger.warning('Custom bot stop failed during deletion')
                if session:
                    temp = None
                    try:
                        temp = Client(f'privacy_{uid}', api_id=API_ID, api_hash=API_HASH, session_string=dcs(session), in_memory=True)
                        await temp.connect()
                        await temp.log_out()
                        revoked = True
                    except Exception:
                        logger.warning('Unable to revoke session during deletion')
                        if temp:
                            try: await temp.disconnect()
                            except Exception: pass
                await users_collection.delete_many({'user_id': uid})
                for path in (f'{uid}.jpg', f'{uid}_client.session', f'{uid}_client.session-journal', f'user_{uid}.session', f'user_{uid}.session-journal'):
                    try: os.remove(path)
                    except FileNotFoundError: pass
                waiting.pop(uid, None)
                input_languages.pop(uid, None)
            except Exception:
                logger.warning('Account deletion incomplete or uncertain')
                body = (('डेटा हटाना पूरा होने की पुष्टि नहीं हुई; कुछ डेटा हट चुका हो सकता है। Telegram Settings → Devices जाँचें और दोबारा अनुरोध करें। बिलिंग रिकॉर्ड और बैकअप रह सकते हैं।') if hi else
                        ('Could not confirm deletion completed; some data may already be deleted. Check Telegram Settings → Devices and request deletion again. Billing records and backups may remain.'))
                await query.message.edit_text(page('⚠️ डेटा हटाना' if hi else '⚠️ 𝗗𝗘𝗟𝗘𝗧𝗘 𝗗𝗔𝗧𝗔', body), reply_markup=markup([], 'account', language), parse_mode=ParseMode.HTML)
                return
            if hi:
                extra = 'टेलीग्राम सेशन रद्द हुआ।' if revoked else 'Telegram Settings → Devices जाँचें और बचा हुआ सेशन रद्द करें।'
                body = f'✅ <b>अकाउंट सेटिंग्स, सेव credentials, मिरर और सेव टास्क/इतिहास हटाए गए।</b> {extra} बिलिंग रिकॉर्ड और बैकअप रखे जा सकते हैं।'
            else:
                extra = 'Telegram session revoked.' if revoked else 'Check Telegram Settings → Devices and revoke any remaining session.'
                body = f'✅ <b>Account settings, stored credentials, mirror and saved jobs/history deleted.</b> {extra} Billing records and backups may be retained.'
            await query.message.edit_text(page('🗑️ डेटा हटाया' if hi else '🗑️ 𝗗𝗔𝗧𝗔 𝗗𝗘𝗟𝗘𝗧𝗘𝗗', body), reply_markup=markup([], 'home', language), parse_mode=ParseMode.HTML)



async def delete_bot_token_input(message, language):
    """Best-effort deletion; never include token or exception text in output."""
    try:
        await message.delete()
    except Exception as exc:
        logger.warning('Could not delete custom bot input: %s', type(exc).__name__)
        warning = ('⚠️ बॉट आपका टोकन संदेश नहीं हटा सका। इसे इस चैट से स्वयं हटाएँ; उजागर हुआ हो तो BotFather से टोकन रद्द करें।' if language == 'hi' else
                   '⚠️ The bot could not delete your token message. Delete it yourself in this chat; revoke the token with BotFather if exposed.')
        try:
            await message.reply_text(warning)
        except Exception:
            logger.warning('Could not deliver token deletion warning')


async def handle_custom_bot_input(message, uid, text, language):
    from plugins.batch_queue import lock_for
    await delete_bot_token_input(message, language)
    hi = language == 'hi'
    cancel = markup([[('❌ 𝗖𝗔𝗡𝗖𝗘𝗟', 'cancel')]], language=language)
    if not re.fullmatch(r'[0-9]{1,20}:[A-Za-z0-9_-]{20,256}', text):
        body = ('⚠️ टोकन का फ़ॉर्मैट सही नहीं है। फिर भेजें या रद्द करें।' if hi else
                '⚠️ Invalid token format. Try again or tap Cancel.')
        await message.reply_text(body, reply_markup=cancel)
        return
    async with lock_for(uid):
        if waiting.get(uid) != 'addbot':
            return
        if batch.is_user_active(uid) or get_user_step(uid):
            body = ('⚠️ कस्टम बॉट बदलने से पहले चालू टास्क रोकें या लॉगिन पूरा करें।' if hi else
                    '⚠️ Stop your active task or finish login before replacing the custom bot.')
            await message.reply_text(body, reply_markup=cancel)
            return
        import os
        from plugins.mirror import stop_mirror
        try:
            await stop_mirror(uid, drain=True)
            old = batch.UB.pop(uid, None)
            if old:
                await old.stop()
            for path in (f'user_{uid}.session', f'user_{uid}.session-journal'):
                try: os.remove(path)
                except FileNotFoundError: pass
            if not await save_user_bot(uid, text):
                raise RuntimeError('Storage was not confirmed')
        except Exception:
            logger.warning('Custom bot setup incomplete or uncertain')
            waiting.pop(uid, None)
            input_languages.pop(uid, None)
            body = (('⚠️ टोकन सेव होने की पुष्टि नहीं हुई; यह सेव हो चुका हो सकता है और मिरर बंद हो सकता है। अकाउंट स्थिति जाँचें। '
                     'स्थिति से यह पता नहीं चलता कि कौन-सा टोकन सेव है। उजागर टोकन BotFather से रद्द करें और फिर कोशिश के लिए कस्टम बॉट खोलें।') if hi else
                    ('⚠️ Could not confirm token storage; it may already be saved and the mirror may have stopped. Check Account Status. '
                     'Status does not identify which token is stored. Revoke an exposed token with BotFather and open Custom Bot to try again.'))
            await message.reply_text(page('⚠️ कस्टम बॉट' if hi else '⚠️ 𝗖𝗨𝗦𝗧𝗢𝗠 𝗕𝗢𝗧', body), reply_markup=markup([], 'custombot', language), parse_mode=ParseMode.HTML)
            return
        waiting.pop(uid, None)
        input_languages.pop(uid, None)
        body = (('✅ टोकन सेव हुआ। टेलीग्राम से इसकी वैधता जाँची नहीं गई है; इस्तेमाल करते समय कनेक्शन असफल हो सकता है।') if hi else
                ('✅ Token saved. Its validity was not checked with Telegram; connecting during use may still fail.'))
        await message.reply_text(page('🤖 कस्टम बॉट' if hi else '🤖 𝗖𝗨𝗦𝗧𝗢𝗠 𝗕𝗢𝗧', body), reply_markup=markup([], 'custombot', language), parse_mode=ParseMode.HTML)


def _waiting_filter(_, __, message):
    return bool(message.from_user and message.from_user.id in waiting)


@app.on_message(filters.private & filters.create(_waiting_filter) & (filters.text | filters.photo) & ~filters.command('start'), group=-1)
async def menu_input(client, message):
    uid = message.from_user.id
    action = waiting.get(uid)
    text = (message.text or '').strip()
    if uid in busy_inputs:
        if action == 'addbot':
            language = input_languages.get(uid, 'en')
            await delete_bot_token_input(message, language)
            await message.reply_text('⏳ आपका पिछला इनपुट प्रोसेस हो रहा है। प्रतीक्षा करें।' if language == 'hi' else '⏳ Your previous input is still being processed. Please wait.')
        else:
            await message.reply_text('⏳ Your previous input is still being processed. Please wait.')
        message.stop_propagation()
        return
    busy_token = object()
    busy_inputs[uid] = busy_token
    try:
        if action and action.startswith('owner_') and uid not in OWNER_ID:
            waiting.pop(uid, None)
            await message.reply_text('⛔ Owner only.')
            return
        if action == 'gift_reference':
            from plugins.gift import submit_reference
            if not await submit_reference(client, message, uid, text):
                return
        elif action == 'owner_grant':
            from plugins.pay import grant_invoice
            from utils.func import premium_users_collection, db
            parts = text.split()
            if len(parts) != 2 or not all(x.isdigit() for x in parts) or not 1 <= int(parts[1]) <= 3650:
                await message.reply_text('⚠️ Send a user ID and 1–3650 days.')
                return
            target, days = map(int, parts)
            await premium_users_collection.create_index('user_id', unique=True)
            audit = await db['admin_audit'].insert_one({'owner_id': uid, 'action': 'grant', 'target_id': target, 'days': days, 'status': 'processing'})
            charge = 'manual:' + str(audit.inserted_id)
            result = await grant_invoice({'user_id': target, 'days': days}, charge)
            if not result or charge not in result.get('applied_payments', []):
                raise RuntimeError('Premium grant not confirmed')
            try:
                await db['admin_audit'].update_one({'_id': audit.inserted_id}, {'$set': {'status': 'completed'}})
            except Exception:
                logger.exception('Grant succeeded but audit status update failed')
            await message.reply_text(f'✅ Premium extended by {days} days for {target}.')
        elif action == 'owner_revoke':
            from utils.func import premium_users_collection, db
            if not text.isdigit():
                await message.reply_text('⚠️ Send a numeric user ID.')
                return
            target = int(text)
            result = await premium_users_collection.update_one({'user_id': target}, {'$set': {'subscription_end': datetime.utcnow()}})
            await db['admin_audit'].insert_one({'owner_id': uid, 'action': 'revoke', 'target_id': target})
            await message.reply_text('✅ Premium revoked.' if result.matched_count else 'ℹ️ No premium record found.')
        elif action == 'owner_broadcast':
            from utils.func import premium_users_collection
            if not text or len(text) > 3500:
                await message.reply_text('⚠️ Send text of up to 3500 characters.')
                return
            sent = failed = 0
            async for item in premium_users_collection.find({'subscription_end': {'$gt': datetime.utcnow()}}, {'user_id': 1}):
                try:
                    await client.send_message(item['user_id'], page('📢 𝗔𝗡𝗡𝗢𝗨𝗡𝗖𝗘𝗠𝗘𝗡𝗧', html.escape(text)), parse_mode=ParseMode.HTML)
                    sent += 1
                except Exception:
                    failed += 1
                await asyncio.sleep(0.1)
            await message.reply_text(f'✅ Broadcast finished. Sent: {sent}; failed: {failed}.')
        elif action == 'thumbnail':
            if not message.photo:
                await message.reply_text('⚠️ Please send a photo.')
                return
            await message.download(file_name=f'{uid}.jpg')
        elif action == 'addbot':
            await handle_custom_bot_input(message, uid, text, input_languages.get(uid, 'en'))
            return
        elif action in ('video', 'audio'):
            from config import ENABLE_EXTERNAL_DOWNLOADS
            if not ENABLE_EXTERNAL_DOWNLOADS:
                waiting.pop(uid, None)
                download_quality.pop(uid, None)
                await message.reply_text('⚠️ External downloads are disabled by the operator.')
                return
            from plugins.ytdl import valid_external_url
            if not valid_external_url(text):
                await message.reply_text('⚠️ Please send a valid HTTP(S) URL.')
                return
            from shared_client import client as telethon_client
            from plugins.ytdl import process_audio, process_video, ongoing_downloads, user_progress, cookies_for_url
            if uid in ongoing_downloads:
                await message.reply_text('⚠️ A download is already running.')
                return
            # Reserve synchronously before any network await, including lookup.
            ongoing_downloads[uid] = True
            quality = download_quality.pop(uid, 'auto')
            waiting.pop(uid, None)
            if busy_inputs.get(uid) is busy_token:
                busy_inputs.pop(uid, None)
            try:
                event = await telethon_client.get_messages(uid, ids=message.id)
                if not event or event.sender_id != uid or event.chat_id != uid:
                    await message.reply_text('⚠️ Could not verify the input message. Please try again.')
                    return
                cookies = cookies_for_url(text)
                if action == 'audio':
                    await process_audio(telethon_client, event, text, cookies)
                else:
                    await process_video(telethon_client, event, text, cookies, check_duration_and_size=True, quality=quality)
            finally:
                ongoing_downloads.pop(uid, None)
                user_progress.pop(uid, None)
            return
        elif action == 'mirror':
            from plugins.mirror import start_mirror
            if not re.fullmatch(r'(?:@[A-Za-z0-9_]{4,32}|-?\d{5,20})', text):
                await message.reply_text('⚠️ Send @username or a numeric chat ID.')
                return
            source = int(text) if re.fullmatch(r'-?\d+', text) else text
            from plugins.batch_queue import lock_for
            async with lock_for(uid):
                if waiting.get(uid) != action:
                    return
                ok, reason = await start_mirror(uid, source)
            waiting.pop(uid, None)
            await message.reply_text(page('🪞 𝗠𝗜𝗥𝗥𝗢𝗥', html.escape(reason)), reply_markup=markup([], 'save'), parse_mode=ParseMode.HTML)
            return
        elif action == 'destination':
            if not re.fullmatch(r'-?\d+(?:/\d+)?', text):
                await message.reply_text('⚠️ Send a numeric chat ID or chat ID/topic ID.')
                return
            from plugins.mirror import active as active_mirrors
            mirror = active_mirrors.get(uid)
            if mirror and mirror['source_id'] == int(text.split('/', 1)[0]):
                await message.reply_text('⚠️ Destination cannot be the mirror source. Stop the mirror first.')
                return
            await save_user_data(uid, 'chat_id', text)
        elif action in ('rename', 'caption', 'prefix', 'suffix'):
            if not text or len(text) > 1024:
                await message.reply_text('⚠️ Send text between 1 and 1024 characters.')
                return
            await save_user_data(uid, 'rename_tag' if action == 'rename' else action, text)
        elif action == 'deletewords':
            if not text: return
            data = await get_user_data(uid) or {}
            await save_user_data(uid, 'delete_words', list(dict.fromkeys(data.get('delete_words', []) + text.split())))
        elif action == 'replacewords':
            if '=' not in text or not text.split('=', 1)[0].strip():
                await message.reply_text('⚠️ Use old=new.')
                return
            old, new = (s.strip() for s in text.split('=', 1))
            if len(old) > 100 or len(new) > 500 or '.' in old or old.startswith('$'):
                await message.reply_text('⚠️ Invalid rule. The original word cannot contain a dot or start with $.')
                return
            data = await get_user_data(uid) or {}
            rules = data.get('replacement_words', {})
            rules[old] = new
            await save_user_data(uid, 'replacement_words', rules)
        waiting.pop(uid, None)
        await message.reply_text(page('✅ 𝗦𝗔𝗩𝗘𝗗', 'Your selection was saved.'), reply_markup=markup([], 'home'), parse_mode=ParseMode.HTML)
    except Exception:
        logger.error('Menu input failed for action %s', action)
        waiting.pop(uid, None)
        input_languages.pop(uid, None)
        download_quality.pop(uid, None)
        await message.reply_text(page('⚠️ 𝗘𝗥𝗥𝗢𝗥', 'Could not complete this action. Please try again.'), reply_markup=markup([], 'home'), parse_mode=ParseMode.HTML)
    finally:
        if busy_inputs.get(uid) is busy_token:
            busy_inputs.pop(uid, None)
        message.stop_propagation()
