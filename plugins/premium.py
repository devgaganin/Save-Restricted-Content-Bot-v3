# Copyright (c) 2025 Gagan : https://github.com/devgaganin.
# Licensed under the GNU General Public License v3.0.
# See LICENSE file in the repository root for full license text.

from shared_client import client as bot_client
from telethon import events
from config import OWNER_ID
from utils.func import is_private_chat


@bot_client.on(events.NewMessage(pattern=r'^/add(?:@\w+)?(?:\s|$)'))
async def add_premium_handler(event):
    if await is_private_chat(event) and event.sender_id in OWNER_ID:
        await event.respond('👑 Use Owner Panel → Grant to extend premium safely.')
