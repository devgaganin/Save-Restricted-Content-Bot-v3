# Copyright (c) 2025 devgagan : https://github.com/devgaganin.
# Licensed under the GNU General Public License v3.0.
# See LICENSE file in the repository root for full license text.

import asyncio
import importlib
import logging
import os
import signal
import sys

logger = logging.getLogger(__name__)


def validate_configuration():
    from config import API_ID, API_HASH, BOT_TOKEN, MONGO_DB, OWNER_ID, MASTER_KEY, IV_KEY
    if not str(API_ID).isdigit() or not all((API_HASH, BOT_TOKEN, MONGO_DB, OWNER_ID)):
        raise RuntimeError('Set API_ID, API_HASH, BOT_TOKEN, MONGO_DB and OWNER_ID before starting.')
    legacy = MASTER_KEY == 'gK8HzLfT9QpViJcYeB5wRa3DmN7P2xUq' or IV_KEY == 's7Yx5CpVmE3F'
    if legacy and os.getenv('ALLOW_INSECURE_LEGACY_KEYS', 'false').lower() != 'true':
        raise RuntimeError('Set unique MASTER_KEY and IV_KEY; see README for existing-session migration.')
    if not legacy and (len(MASTER_KEY) < 16 or len(IV_KEY) < 12):
        raise RuntimeError('MASTER_KEY must be at least 16 characters and IV_KEY at least 12 characters.')
    if legacy:
        logger.warning('Insecure legacy encryption keys in use; migrate encrypted data before rotating them.')


async def load_and_run_plugins():
    validate_configuration()
    from shared_client import start_client
    await start_client()
    plugins = sorted(f[:-3] for f in os.listdir('plugins') if f.endswith('.py') and f != '__init__.py')
    for plugin in plugins:
        module = importlib.import_module(f'plugins.{plugin}')
        if hasattr(module, f'run_{plugin}_plugin'):
            await getattr(module, f'run_{plugin}_plugin')()


async def shutdown():
    """Close connected clients; live mirror subscriptions remain saved for restart."""
    reminders = sys.modules.get('plugins.reminders')
    if reminders:
        await reminders.shutdown_reminders()
    queue = sys.modules.get('plugins.batch_queue')
    if queue:
        await queue.shutdown_workers()
    batch = sys.modules.get('plugins.batch')
    login = sys.modules.get('plugins.login')
    clients = []
    if batch:
        clients.extend(list(batch.UB.values()) + list(batch.UC.values()))
        batch.UB.clear()
        batch.UC.clear()
    if login:
        for item in login.login_cache.values():
            if item.get('temp_client'):
                clients.append(item['temp_client'])
    shared = sys.modules.get('shared_client')
    if shared:
        clients.extend((shared.userbot, shared.app))
    for client in clients:
        try:
            if client.is_connected:
                if getattr(client, 'is_initialized', False):
                    await client.stop()
                else:
                    await client.disconnect()
        except Exception:
            logger.exception('Could not stop a client')
    if shared and shared.client.is_connected():
        try: await shared.client.disconnect()
        except Exception: logger.exception('Could not disconnect Telethon')
    helpers = sys.modules.get('utils.func')
    if helpers:
        helpers.mongo_client.close()


async def main():
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):
            pass
    try:
        await load_and_run_plugins()
        await stop.wait()
    finally:
        await shutdown()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except (RuntimeError, ValueError) as exc:
        print(f'Startup error: {exc}')
        raise SystemExit(1)
