# Copyright (c) 2025 devgagan : https://github.com/devgaganin.  
# Licensed under the GNU General Public License v3.0.  
# See LICENSE file in the repository root for full license text.

from telethon import TelegramClient
from config import API_ID, API_HASH, BOT_TOKEN, STRING
from pyrogram import Client
import sys

client = None
app = None
userbot = None

async def start_client():
    global client, app, userbot
    
    if not API_ID or not API_HASH or not BOT_TOKEN:
        print("ERROR: API_ID, API_HASH, and BOT_TOKEN are required!")
        print("Please set them in Replit Secrets.")
        sys.exit(1)
    
    try:
        client = TelegramClient("telethonbot", int(API_ID), API_HASH)
        app = Client("pyrogrambot", api_id=int(API_ID), api_hash=API_HASH, bot_token=BOT_TOKEN)
        
        if STRING:
            userbot = Client("4gbbot", api_id=int(API_ID), api_hash=API_HASH, session_string=STRING)
        
        if not client.is_connected():
            await client.start(bot_token=BOT_TOKEN)
            print("SpyLib started...")
        
        if STRING and userbot:
            try:
                await userbot.start()
                print("Userbot started...")
            except Exception as e:
                print(f"Warning: Could not start userbot - {e}")
                userbot = None
        
        await app.start()
        print("Pyro App Started...")
        
        return client, app, userbot
    except Exception as e:
        print(f"Error starting clients: {e}")
        sys.exit(1)
