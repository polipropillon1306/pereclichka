import os
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
GOOGLE_SHEET_URL = os.getenv("GOOGLE_SHEET_URL", "")

_raw_account_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json")
GOOGLE_SERVICE_ACCOUNT_FILE = _raw_account_file if os.path.isabs(_raw_account_file) else os.path.join(BASE_DIR, _raw_account_file)

POLL_TIME = os.getenv("POLL_TIME", "20:00")
CHECK_TIME = os.getenv("CHECK_TIME", "11:00")
TIMEZONE = "Europe/Moscow"

_raw_db_path = os.getenv("DB_PATH", "bot_data.db")
DB_PATH = _raw_db_path if os.path.isabs(_raw_db_path) else os.path.join(BASE_DIR, _raw_db_path)

# Список Telegram ID администраторов с правом запуска команд
ADMIN_IDS = [
    int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()
]

# Разрешенные группы/чаты для работы бота
_raw_allowed = os.getenv("ALLOWED_CHAT_IDS", "").split(",")
ALLOWED_CHAT_IDS = [
    int(x.strip()) for x in _raw_allowed if x.strip().lstrip("-").isdigit()
]
