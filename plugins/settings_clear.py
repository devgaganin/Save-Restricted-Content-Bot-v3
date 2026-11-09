"""Explicit, one-shot removal of individual content settings."""
import copy
import re
import secrets
import time

from pyrogram.enums import ParseMode
from plugins.batch_queue import lock_for
from plugins import localization
from utils.func import users_collection

# Only content preferences; never credentials, billing, or reminder preferences.
FIELDS = {
    'destination': ('chat_id', 'Destination'),
    'rename': ('rename_tag', 'Rename tag'),
    'caption': ('caption', 'Caption'),
    'prefix': ('prefix', 'Prefix'),
    'suffix': ('suffix', 'Suffix'),
    'filter': ('media_filter', 'Media filter'),
    'original_caption': ('remove_caption', 'Original caption preference'),
    'deletewords': ('delete_words', 'Deleted words'),
    'replacewords': ('replacement_words', 'Replacement rules'),
}
confirmations = {}
CONFIRM_SECONDS = 600


async def show_choices(message, uid, language='en'):
    from plugins.menu import markup, page
    async with lock_for(uid):
        confirmations.pop(uid, None)
    rows = []
    items = list(FIELDS.items())
    for i in range(0, len(items), 2):
        rows.append([(label, 'setting_clear:' + slug) for slug, (_, label) in items[i:i + 2]])
    body = (('हटाने के लिए एक कंटेंट सेटिंग चुनें। अगली स्क्रीन पर पुष्टि माँगी जाएगी। '
             'लॉगिन जानकारी और भुगतान विवरण नहीं बदलेंगे। '
             'बदलाव चालू टास्क को प्रभावित कर सकते हैं; बैच का डेस्टिनेशन शुरू होने पर तय होता है।') if language == 'hi' else
            ('Choose a content setting to clear. You will be asked to confirm. '
             'Credentials and payment information are not affected. '
             'Changes may affect an active task; a batch destination is captured when it starts.'))
    await message.edit_text(page('🧹 सेटिंग हटाएँ' if language == 'hi' else '🧹 𝗖𝗟𝗘𝗔𝗥 𝗦𝗘𝗧𝗧𝗜𝗡𝗚', body), reply_markup=markup(rows, 'settings', language), parse_mode=ParseMode.HTML)


async def ask_clear(message, uid, slug, language='en'):
    from plugins.menu import markup, page
    field = FIELDS.get(slug)
    if not field:
        await message.edit_text(page('⚠️ सेटिंग्स' if language == 'hi' else '⚠️ 𝗦𝗘𝗧𝗧𝗜𝗡𝗚𝗦', 'सेटिंग मान्य नहीं है।' if language == 'hi' else 'Invalid setting.'), reply_markup=markup([], 'settings_clear', language), parse_mode=ParseMode.HTML)
        return
    key, label = field
    if language == 'hi':
        label = localization.HI_SETTING_LABELS[slug]
    async with lock_for(uid):
        # A failing DB read must not be confused with an empty setting.
        data = await users_collection.find_one({'user_id': uid}, {key: 1, '_id': 0}) or {}
        if key not in data:
            confirmations.pop(uid, None)
            found = False
        else:
            token = secrets.token_hex(8)
            confirmations[uid] = (token, key, copy.deepcopy(data[key]), time.monotonic() + CONFIRM_SECONDS)
            found = True
    if not found:
        body = f'{label} सेट नहीं है।' if language == 'hi' else f'{label} is not set.'
        await message.edit_text(page('ℹ️ सेटिंग्स' if language == 'hi' else 'ℹ️ 𝗦𝗘𝗧𝗧𝗜𝗡𝗚𝗦', body), reply_markup=markup([], 'settings_clear', language), parse_mode=ParseMode.HTML)
        return
    body = (f'🧹 <b>{label}</b> हटाएँ? केवल यह सेटिंग प्रभावित होगी। पुष्टि से पहले मान बदलने पर अनुरोध अस्वीकार होगा।' if language == 'hi' else f'🧹 Clear <b>{label}</b>? This affects only this setting. If the value changes before confirmation, the request is rejected.')
    await message.edit_text(page('⚠️ पुष्टि करें' if language == 'hi' else '⚠️ 𝗖𝗢𝗡𝗙𝗜𝗥𝗠', body), reply_markup=markup([[('✅ 𝗖𝗟𝗘𝗔𝗥', 'setting_clear_yes:' + token), ('❌ 𝗖𝗔𝗡𝗖𝗘𝗟', 'settings_clear')]], 'settings', language), parse_mode=ParseMode.HTML)


async def clear(uid, token):
    async with lock_for(uid):
        item = confirmations.get(uid)
        if not item or item[3] < time.monotonic() or not isinstance(token, str) or not re.fullmatch(r'[0-9a-f]{16}', token) or not secrets.compare_digest(item[0], token):
            if item and item[3] < time.monotonic():
                confirmations.pop(uid, None)
            return False, 'Confirmation expired. Select the setting again.'
        confirmations.pop(uid, None)
        _, key, original, _ = item
        # Compare and unset atomically so an old confirmation cannot remove a
        # value changed after the user reviewed the prompt. $exists distinguishes
        # a stored null from an absent field.
        selector = {'user_id': uid, '$and': [{key: {'$exists': True}}, {key: {'$eq': original}}]}
        result = await users_collection.update_one(selector, {'$unset': {key: ''}})
        if not result.modified_count:
            return False, 'Setting changed or was already cleared. Review it and try again.'
        return True, 'Setting cleared.'
