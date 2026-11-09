# Copyright (c) 2025 devgagan : https://github.com/devgaganin.
# Licensed under the GNU General Public License v3.0.
# See LICENSE file in the repository root for full license text.

"""Retired legacy settings handlers and filename customization helper.

Old buttons must not change credentials or settings, even if Telegram still
shows them. The current private menus implement the supported flows.
"""
import os
import random
import string
from telethon import events
from shared_client import client as gf
from utils.func import get_user_data_key

VIDEO_EXTENSIONS = {
    'mp4', 'mkv', 'avi', 'mov', 'wmv', 'flv', 'webm',
    'mpeg', 'mpg', '3gp'
}

LEGACY_CALLBACKS = (
    b'setchat', b'setrename', b'setcaption', b'setreplacement', b'addsession',
    b'delete', b'setthumb', b'logout', b'reset', b'remthumb'
)


@gf.on(events.NewMessage(incoming=True, pattern=r'^/settings(?:@\w+)?(?:\s.*)?$'))
async def settings_command(event):
    if not event.is_private:
        await event.respond('⚠️ Open the bot in a private chat and use the Settings menu.')
        return
    await event.respond('⚙️ Open Settings from the private menu. Old settings buttons are disabled.')


@gf.on(events.CallbackQuery(pattern=b'^(?:setchat|setrename|setcaption|setreplacement|addsession|delete|setthumb|logout|reset|remthumb)$'))
async def callback_query_handler(event):
    # Never prompt for a session string or mutate data from a stale button.
    await event.answer('This old button is disabled. Open the private Settings menu.', alert=True)


def generate_random_name(length=7):
    characters = string.ascii_letters + string.digits
    return ''.join(random.choice(characters) for _ in range(length))


async def rename_file(file, sender, edit):
    try:
        delete_words = await get_user_data_key(sender, 'delete_words', [])
        custom_rename_tag = await get_user_data_key(sender, 'rename_tag', '')
        replacements = await get_user_data_key(sender, 'replacement_words', {})

        last_dot_index = str(file).rfind('.')
        if last_dot_index != -1 and last_dot_index != 0:
            ggn_ext = str(file)[last_dot_index + 1:]
            if ggn_ext.isalpha() and len(ggn_ext) <= 9:
                if ggn_ext.lower() in VIDEO_EXTENSIONS:
                    original_file_name = str(file)[:last_dot_index]
                    file_extension = 'mp4'
                else:
                    original_file_name = str(file)[:last_dot_index]
                    file_extension = ggn_ext
            else:
                original_file_name = str(file)[:last_dot_index]
                file_extension = 'mp4'
        else:
            original_file_name = str(file)
            file_extension = 'mp4'

        for word in delete_words:
            original_file_name = original_file_name.replace(word, '')

        for word, replace_word in replacements.items():
            original_file_name = original_file_name.replace(word, replace_word)

        new_file_name = f'{original_file_name} {custom_rename_tag}.{file_extension}'

        os.rename(file, new_file_name)
        return new_file_name
    except Exception as e:
        print(f"Rename error: {e}")
        return file

