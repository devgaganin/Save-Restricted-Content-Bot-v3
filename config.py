import os
from dotenv import load_dotenv

load_dotenv()


def _getenv(*names, default=""):
    for name in names:
        value = os.getenv(name)
        if value is not None and str(value).strip() != "":
            return str(value).strip()
    return default


def _getint(*names, default=0):
    value = _getenv(*names, default=str(default))
    try:
        return int(value)
    except Exception:
        return default


def _get_owner_ids():
    raw = _getenv("OWNER_ID", "ADMIN_ID", default="")
    if not raw:
        return []
    if "," in raw:
        parts = [part.strip() for part in raw.split(",")]
    else:
        parts = [part.strip() for part in raw.split()]
    ids = []
    for part in parts:
        if not part:
            continue
        try:
            ids.append(int(part))
        except Exception:
            pass
    return ids


INST_COOKIES = """
# write up here insta cookies
"""

YTUB_COOKIES = """
# write here yt cookies
"""


# Core bot/database config
API_ID = _getenv("API_ID", "TG_API_ID", default="")
API_HASH = _getenv("API_HASH", "TG_API_HASH", default="")
BOT_TOKEN = _getenv("BOT_TOKEN", "TG_BOT_TOKEN", default="")
MONGO_DB = _getenv("MONGO_DB", default="")
DB_NAME = _getenv("DB_NAME", default="telegram_downloader")
STORAGE_CHANNEL_ID = _getint("TG_STORAGE_CHANNEL", "LOG_GROUP", default=0)
BACKUP_CHANNEL_ID = _getint("TG_BACKUP_CHANNEL", default=0)
TEMP_DOWNLOAD_DIR = _getenv(
    "TEMP_DIR",
    default="/dev/shm/telegram_vault_tmp" if os.path.isdir("/dev/shm") else "/tmp/telegram_vault_tmp"
)


# Owner / control settings
OWNER_ID = _get_owner_ids()
STRING = _getenv("STRING", default=None)
LOG_GROUP = _getint("LOG_GROUP", "TG_STORAGE_CHANNEL", "TG_BACKUP_CHANNEL", default=0)
FORCE_SUB = _getint("FORCE_SUB", "CHANNEL_ID", default=0)


# Security keys
MASTER_KEY = _getenv("MASTER_KEY", "ENCRYPTION_KEY", default="gK8HzLfT9QpViJcYeB5wRa3DmN7P2xUq")
IV_KEY = _getenv("IV_KEY", "DB_PASSWORD", default="s7Yx5CpVmE3F")


# Cookies
YT_COOKIES = _getenv("YT_COOKIES", default=YTUB_COOKIES)
INSTA_COOKIES = _getenv("INSTA_COOKIES", default=INST_COOKIES)


# Limits
FREEMIUM_LIMIT = _getint("FREEMIUM_LIMIT", default=0)
PREMIUM_LIMIT = _getint("PREMIUM_LIMIT", default=500)


# UI / links
JOIN_LINK = _getenv("JOIN_LINK", default="https://t.me/team_spy_pro")
ADMIN_CONTACT = _getenv("ADMIN_CONTACT", default="https://t.me/username_of_admin")


# Premium plan config
P0 = {
    "d": {
        "s": _getint("PLAN_D_S", default=1),
        "du": _getint("PLAN_D_DU", default=1),
        "u": _getenv("PLAN_D_U", default="days"),
        "l": _getenv("PLAN_D_L", default="Daily"),
    },
    "w": {
        "s": _getint("PLAN_W_S", default=3),
        "du": _getint("PLAN_W_DU", default=1),
        "u": _getenv("PLAN_W_U", default="weeks"),
        "l": _getenv("PLAN_W_L", default="Weekly"),
    },
    "m": {
        "s": _getint("PLAN_M_S", default=5),
        "du": _getint("PLAN_M_DU", default=1),
        "u": _getenv("PLAN_M_U", default="month"),
        "l": _getenv("PLAN_M_L", default="Monthly"),
    },
}
