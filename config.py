import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
GOOGLE_SHEET_URL = os.getenv("GOOGLE_SHEET_URL", "")
GOOGLE_SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json")
POLL_TIME = os.getenv("POLL_TIME", "20:00")
CHECK_TIME = os.getenv("CHECK_TIME", "11:00")
TIMEZONE = "Europe/Moscow"
DB_PATH = "bot_data.db"

# Список Telegram ID администраторов с правом запуска команд
ADMIN_IDS = [
    int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()
]

# Разрешенные группы/чаты для работы бота
_raw_allowed = os.getenv("ALLOWED_CHAT_IDS", "").split(",")
ALLOWED_CHAT_IDS = [
    int(x.strip()) for x in _raw_allowed if x.strip().lstrip("-").isdigit()
]
