# Copyright (c) 2025 devgagan : https://github.com/devgaganin.
# Licensed under the GNU General Public License v3.0.
# See LICENSE file in the repository root for full license text.

import asyncio
import importlib
import os
import sys
import logging
from shared_client import start_client

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def load_and_run_plugins():
    await start_client()
    plugin_dir = "plugins"
    plugins = [
        f[:-3] for f in os.listdir(plugin_dir)
        if f.endswith(".py") and f != "__init__.py" and not f.startswith(".")
    ]

    for plugin in plugins:
        try:
            module = importlib.import_module(f"plugins.{plugin}")
            if hasattr(module, f"run_{plugin}_plugin"):
                logger.info(f"Running {plugin} plugin...")
                await getattr(module, f"run_{plugin}_plugin")()
        except Exception as e:
            logger.error(f"Error running {plugin} plugin: {e}")

async def main():
    await load_and_run_plugins()
    await asyncio.Event().wait()  # Wait indefinitely

if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    logger.info("Starting clients ...")
    try:
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    except Exception as e:
        logger.error(f"Error: {e}")
        sys.exit(1)
    finally:
        try:
            loop.close()
        except Exception:
            pass
