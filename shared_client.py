# Copyright (c) 2025 devgagan : https://github.com/devgaganin.  
# Licensed under the GNU General Public License v3.0.  
# See LICENSE file in the repository root for full license text.

from telethon import TelegramClient
from config import API_ID, API_HASH, BOT_TOKEN, STRING
from pyrogram import Client
import sys

client = TelegramClient("telethonbot", API_ID, API_HASH)
app = Client("pyrogrambot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
userbot = Client("4gbbot", api_id=API_ID, api_hash=API_HASH, session_string=STRING)

async def start_client():
    if not client.is_connected():
        await client.start(bot_token=BOT_TOKEN)
        print("Telegram client started...")
    if STRING:
        try:
            await userbot.start()
            print("Userbot started...")
        except Exception as e:
            print(f"Hey honey!! check your premium string session, it may be invalid of expire {e}")
            sys.exit(1)
    await app.start()
    print("Pyro App Started...")
    # Keep Telegram's command picker simple; legacy commands still work.
    try:
        from pyrogram.types import BotCommand
        await app.set_bot_commands([BotCommand("start", "Open the menu")])
    except Exception as exc:
        print(f"Could not update command menu: {exc}")
    return client, app, userbot

