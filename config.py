# Copyright (c) 2025 devgagan : https://github.com/devgaganin.  
# Licensed under the GNU General Public License v3.0.  
# See LICENSE file in the repository root for full license text.

import os
from dotenv import load_dotenv

load_dotenv()

# VPS --- FILL COOKIES 🍪 in """ ... """ 

INST_COOKIES = """
# wtite up here insta cookies
"""

YTUB_COOKIES = """
# write here yt cookies
"""

API_ID = os.getenv("API_ID", "24090485")
API_HASH = os.getenv("API_HASH", "b056e6499bc0d4a81ab375773ac1170c")
BOT_TOKEN = os.getenv("BOT_TOKEN", "7876972067:AAFp1mrLDdAVjyBPKNb_9s98BibiRNOSOfQ")
MONGO_DB = os.getenv("MONGO_DB", "mongodb+srv://nguyenkhactam5:<db_password>@kanereaction.zhqqd.mongodb.net/?retryWrites=true&w=majority&appName=kanereaction")
OWNER_ID = list(map(int, os.getenv("OWNER_ID", "6383614933").split())) # list seperated via space
DB_NAME = os.getenv("DB_NAME", "download_content")
STRING = os.getenv("STRING", None) # optional
LOG_GROUP = int(os.getenv("LOG_GROUP", "-1002627666364")) # optional with -100
FORCE_SUB = int(os.getenv("FORCE_SUB", "-1002472444792")) # optional with -100
MASTER_KEY = os.getenv("MASTER_KEY", "gK8HzLfT9QpViJcYeB5wRa3DmN7P2xUq") # for session encryption
IV_KEY = os.getenv("IV_KEY", "s7Yx5CpVmE3F") # for decryption
YT_COOKIES = os.getenv("YT_COOKIES", YTUB_COOKIES)
INSTA_COOKIES = os.getenv("INSTA_COOKIES", INST_COOKIES)
FREEMIUM_LIMIT = int(os.getenv("FREEMIUM_LIMIT", "300"))
PREMIUM_LIMIT = int(os.getenv("PREMIUM_LIMIT", "500"))
JOIN_LINK = os.getenv("JOIN_LINK", "https://t.me/team_spy_pro") # this link for start command message
ADMIN_CONTACT = os.getenv("ADMIN_CONTACT", "https://t.me/username_of_admin")

