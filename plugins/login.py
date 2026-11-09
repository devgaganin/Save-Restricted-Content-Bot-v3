# Copyright (c) 2025 devgagan : https://github.com/devgaganin.
# Licensed under the GNU General Public License v3.0.
# See LICENSE file in the repository root for full license text.

from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import BadRequest, SessionPasswordNeeded, PhoneCodeInvalid, PhoneCodeExpired, MessageNotModified
import logging
import os
from config import API_HASH, API_ID
from shared_client import app as bot
from utils.func import save_user_session, get_user_data, remove_user_session, save_user_bot, remove_user_bot
from utils.encrypt import ecs, dcs
from plugins.batch import UB, UC
from plugins import localization
from utils.custom_filters import login_in_progress, set_user_step, get_user_step
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
model = "Tgwithrohit Saver"

STEP_PHONE = 1
STEP_CODE = 2
STEP_PASSWORD = 3
login_cache = {}

@bot.on_message(filters.command('login') & filters.private)
async def login_command(client, message):
    await message.reply('🔐 Open Account → Login from the menu.')

def login_text(key, language='en'):
    """Static instructions only; never interpolate credentials or provider errors."""
    en, hi = LOGIN_TEXT[key]
    return hi if language == 'hi' else en


LOGIN_TEXT = {
    'phone': ('Please send your phone number with country code\nExample: `+12345678900`',
              'कंट्री कोड सहित अपना फ़ोन नंबर भेजें।\nउदाहरण: `+12345678900`'),
    'processing': ('Processing...', 'प्रोसेस हो रहा है...'),
    'bad_phone': ('❌ Please provide a valid phone number starting with +', '❌ + से शुरू होने वाला सही फ़ोन नंबर भेजें।'),
    'processing_phone': ('🔄 Processing phone number...', '🔄 फ़ोन नंबर प्रोसेस हो रहा है...'),
    'code': ('✅ Telegram sent a verification code. Check your Telegram account or the delivery method Telegram used.\n\nEnter the code separated by spaces, like 1 2 3 4 5:',
             '✅ टेलीग्राम ने वेरिफ़िकेशन कोड भेजा है। अपना टेलीग्राम अकाउंट या टेलीग्राम का चुना हुआ डिलीवरी माध्यम जाँचें।\n\nमिला हुआ कोड स्पेस लगाकर भेजें, जैसे 1 2 3 4 5:'),
    'phone_failed': ('❌ Could not send a verification code. Open Account → Login to try again.',
                     '❌ वेरिफ़िकेशन कोड नहीं भेजा जा सका। दोबारा कोशिश के लिए अकाउंट → लॉगिन खोलें।'),
    'verify_code': ('🔄 Verifying code...', '🔄 कोड सत्यापित हो रहा है...'),
    'password': ('🔒 Two-step verification is enabled.\nPlease enter your password:',
                 '🔒 दो-चरणीय सत्यापन चालू है।\nअपना पासवर्ड भेजें:'),
    'code_failed': ('❌ Verification code is invalid or expired. Open Account → Login to try again.',
                    '❌ वेरिफ़िकेशन कोड गलत है या समाप्त हो गया है। दोबारा कोशिश के लिए अकाउंट → लॉगिन खोलें।'),
    'verify_password': ('🔄 Verifying password...', '🔄 पासवर्ड सत्यापित हो रहा है...'),
    'password_failed': ('❌ Password verification could not be completed. Try again or tap Cancel.',
                        '❌ पासवर्ड सत्यापन पूरा नहीं हो पाया। फिर कोशिश करें या रद्द करें दबाएँ।'),
    'failed': ('❌ Login could not be completed. Check Telegram Settings → Devices for unwanted sessions before trying again via Account → Login.',
               '❌ लॉगिन पूरा नहीं हो पाया। अकाउंट → लॉगिन से फिर कोशिश करने से पहले Telegram Settings → Devices में अनचाहे सेशन जाँचें।'),
    'storage_failed': ('⚠️ Could not confirm session storage. Check Telegram Settings → Devices, revoke any unwanted session, and reconnect.',
                       '⚠️ सेशन सेव होने की पुष्टि नहीं हुई। Telegram Settings → Devices में अनचाहे सेशन हटाएँ और फिर से कनेक्ट करें।'),
    'success': ('✅ Logged in successfully!', '✅ लॉगिन सफल हुआ!'),
    'delete_failed': ('⚠️ The bot could not delete your input. Delete that message yourself in this chat.',
                      '⚠️ बॉट आपका इनपुट हटा नहीं सका। इस चैट में वह संदेश स्वयं हटा दें।'),
}


def cancel_keyboard(language):
    label = localization.button_label('cancel', '❌ 𝗖𝗔𝗡𝗖𝗘𝗟', language)
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data='ui:cancel')]])


async def begin_login(message, user_id, language='en'):
    if get_user_step(user_id) or user_id in login_cache:
        raise RuntimeError('Login already in progress')
    language = language if language in localization.SUPPORTED else 'en'
    set_user_step(user_id, STEP_PHONE)
    try:
        status_msg = await message.reply(login_text('phone', language), reply_markup=cancel_keyboard(language))
    except Exception:
        set_user_step(user_id, None)
        raise
    login_cache[user_id] = {'status_msg': status_msg, 'language': language}


async def store_authorized_session(user_id, temp_client, status_msg):
    """Persist or best-effort revoke an already authorized temporary session."""
    language = login_cache.get(user_id, {}).get('language', 'en')
    try:
        session_string = await temp_client.export_session_string()
        encrypted_session = ecs(session_string)
        saved = await save_user_session(user_id, encrypted_session)
    except Exception as exc:
        # Exception messages can contain credentials or provider responses.
        logger.error('Session export/encryption/storage failed: %s', type(exc).__name__)
        saved = False
    if not saved:
        try:
            await temp_client.log_out()
        except Exception as exc:
            logger.error('Could not revoke session after storage failure: %s', type(exc).__name__)
            try:
                await temp_client.disconnect()
            except Exception:
                pass
        login_cache.pop(user_id, None)
        set_user_step(user_id, None)
        await edit_message_safely(status_msg, login_text('storage_failed', language))
        return False
    old = UC.pop(user_id, None)
    if old:
        try:
            await old.stop()
        except Exception as exc:
            logger.error('Could not close previous user client: %s', type(exc).__name__)
    try:
        await temp_client.disconnect()
    except Exception as exc:
        logger.error('Could not disconnect temporary login client: %s', type(exc).__name__)
    login_cache.pop(user_id, None)
    set_user_step(user_id, None)
    await edit_message_safely(status_msg, login_text('success', language))
    return True


@bot.on_message(filters.command(["setbot", "rembot"]) & filters.private)
async def bot_command_redirect(client, message):
    # Tokens accidentally sent alongside the legacy command are sensitive.
    deletion_failed = False
    if len(message.text.split(maxsplit=1)) > 1:
        try:
            await message.delete()
        except Exception as exc:
            logger.warning('Could not delete legacy bot command input: %s', type(exc).__name__)
            deletion_failed = True
    language = await localization.language_for(message.from_user.id)
    if deletion_failed:
        warning = ('⚠️ यह संदेश स्वयं हटाएँ। टोकन उजागर हुआ हो तो BotFather से रद्द करें।' if language == 'hi' else
                   '⚠️ Delete that message yourself. Revoke the token with BotFather if it was exposed.')
        await message.reply(warning)
    await message.reply('🤖 बॉट प्रबंधित करने के लिए अकाउंट → कस्टम बॉट खोलें।' if language == 'hi' else
                        '🤖 Open Account → Custom Bot in the menu to manage your bot.')


@bot.on_message(login_in_progress & filters.text & filters.private & ~filters.command([
    'start', 'batch', 'cancel', 'login', 'logout', 'stop', 'set', 'pay',
    'redeem', 'gencode', 'generate', 'keyinfo', 'encrypt', 'decrypt', 'keys', 'setbot', 'rembot']))
async def handle_login_steps(client, message):
    # Serialize credential writes with cancel, logout, and account deletion.
    from plugins.batch_queue import lock_for
    uid = message.from_user.id
    observed_step = get_user_step(uid)
    async with lock_for(uid):
        if not observed_step or get_user_step(uid) != observed_step or uid not in login_cache:
            try:
                await message.delete()
            except Exception:
                pass
            return
        await _handle_login_steps(client, message)


async def _abort_login(user_id):
    """Clear state even when disconnect itself fails."""
    cached = login_cache.pop(user_id, {})
    set_user_step(user_id, None)
    temp_client = cached.get('temp_client')
    if temp_client:
        try:
            await temp_client.disconnect()
        except Exception as exc:
            logger.warning('Could not disconnect temporary client: %s', type(exc).__name__)


async def _handle_login_steps(client, message):
    user_id = message.from_user.id
    text = message.text.strip()
    step = get_user_step(user_id)
    language = login_cache[user_id].get('language', 'en')
    try:
        await message.delete()
    except Exception as exc:
        logger.warning('Could not delete sensitive login input: %s', type(exc).__name__)
        try:
            await message.reply(login_text('delete_failed', language))
        except Exception:
            pass
    status_msg = login_cache[user_id].get('status_msg')
    if not status_msg:
        status_msg = await message.reply(login_text('processing', language), reply_markup=cancel_keyboard(language))
        login_cache[user_id]['status_msg'] = status_msg
    try:
        if step == STEP_PHONE:
            if not text.startswith('+'):
                await edit_message_safely(status_msg, login_text('bad_phone', language), language, True)
                return
            await edit_message_safely(status_msg, login_text('processing_phone', language), language, True)
            temp_client = Client(f'temp_{user_id}', api_id=API_ID, api_hash=API_HASH,
                                 device_model=model, in_memory=True)
            login_cache[user_id]['temp_client'] = temp_client
            try:
                await temp_client.connect()
                sent_code = await temp_client.send_code(text)
                login_cache[user_id]['phone'] = text
                login_cache[user_id]['phone_code_hash'] = sent_code.phone_code_hash
                set_user_step(user_id, STEP_CODE)
                await edit_message_safely(status_msg, login_text('code', language), language, True)
            except BadRequest:
                await _abort_login(user_id)
                await edit_message_safely(status_msg, login_text('phone_failed', language))
        elif step == STEP_CODE:
            code = text.replace(' ', '')
            phone = login_cache[user_id]['phone']
            phone_code_hash = login_cache[user_id]['phone_code_hash']
            temp_client = login_cache[user_id]['temp_client']
            try:
                await edit_message_safely(status_msg, login_text('verify_code', language), language, True)
                await temp_client.sign_in(phone, phone_code_hash, code)
                await store_authorized_session(user_id, temp_client, status_msg)
            except SessionPasswordNeeded:
                set_user_step(user_id, STEP_PASSWORD)
                await edit_message_safely(status_msg, login_text('password', language), language, True)
            except (PhoneCodeInvalid, PhoneCodeExpired):
                await _abort_login(user_id)
                await edit_message_safely(status_msg, login_text('code_failed', language))
        elif step == STEP_PASSWORD:
            temp_client = login_cache[user_id]['temp_client']
            try:
                await edit_message_safely(status_msg, login_text('verify_password', language), language, True)
                await temp_client.check_password(text)
                await store_authorized_session(user_id, temp_client, status_msg)
            except BadRequest:
                await edit_message_safely(status_msg, login_text('password_failed', language), language, True)
    except Exception as exc:
        logger.error('Login flow failed: %s', type(exc).__name__)
        await _abort_login(user_id)
        await edit_message_safely(status_msg, login_text('failed', language))


async def edit_message_safely(message, text, language='en', cancel=False):
    """Best effort UI update; retain Cancel only while awaiting login input."""
    try:
        await message.edit(text, reply_markup=cancel_keyboard(language) if cancel else None)
    except MessageNotModified:
        pass
    except Exception as exc:
        logger.error('Could not edit login status: %s', type(exc).__name__)

@bot.on_message(filters.command('cancel') & filters.private)
async def cancel_command(client, message):
    await message.reply('❌ Use the Cancel button in your current flow.')

@bot.on_message(filters.command('logout') & filters.private)
async def logout_command(client, message):
    await message.reply('🚪 Open Account → Logout from the menu.')
