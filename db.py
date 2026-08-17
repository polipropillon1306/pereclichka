import aiosqlite
import logging
from config import DB_PATH, ALLOWED_CHAT_IDS

logger = logging.getLogger(__name__)

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        # Реестр чатов
        await db.execute("""
            CREATE TABLE IF NOT EXISTS chats (
                chat_id INTEGER PRIMARY KEY,
                sheet_url TEXT,
                poll_time TEXT DEFAULT '20:00',
                check_time TEXT DEFAULT '11:00'
            )
        """)
        # Ответы на перекличку по датам
        await db.execute("""
            CREATE TABLE IF NOT EXISTS rollcalls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                target_date TEXT,
                user_id INTEGER,
                username TEXT,
                full_name TEXT,
                status TEXT, -- '+' или '-'
                checked_in INTEGER DEFAULT 0, -- 1 если написал с 06:00 до 11:00
                message_id INTEGER,
                UNIQUE(chat_id, target_date, user_id)
            )
        """)
        # Хранение ID сообщений опроса для редактирования
        await db.execute("""
            CREATE TABLE IF NOT EXISTS poll_messages (
                chat_id INTEGER,
                target_date TEXT,
                message_id INTEGER,
                PRIMARY KEY (chat_id, target_date)
            )
        """)
        await db.commit()

async def register_chat(chat_id: int):
    if chat_id not in ALLOWED_CHAT_IDS:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO chats (chat_id) VALUES (?)", (chat_id,))
        await db.commit()

async def update_chat_sheet(chat_id: int, sheet_url: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE chats SET sheet_url = ? WHERE chat_id = ?", (sheet_url, chat_id))
        await db.commit()

async def get_chat_sheet(chat_id: int) -> str:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT sheet_url FROM chats WHERE chat_id = ?", (chat_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row and row[0] else ""

async def get_all_chats():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT chat_id, sheet_url, poll_time, check_time FROM chats") as cursor:
            return await cursor.fetchall()

async def save_vote(chat_id: int, target_date: str, user_id: int, username: str, full_name: str, status: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO rollcalls (chat_id, target_date, user_id, username, full_name, status)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(chat_id, target_date, user_id) DO UPDATE SET
                status = excluded.status,
                username = excluded.username,
                full_name = excluded.full_name,
                checked_in = CASE WHEN excluded.status = '+' THEN checked_in ELSE 0 END
        """, (chat_id, target_date, user_id, username, full_name, status))
        await db.commit()

async def set_checked_in(chat_id: int, target_date: str, user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            UPDATE rollcalls SET checked_in = 1
            WHERE chat_id = ? AND target_date = ? AND user_id = ? AND status = '+' AND checked_in = 0
        """, (chat_id, target_date, user_id))
        await db.commit()
        return cursor.rowcount > 0

async def get_votes_for_date(chat_id: int, target_date: str):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT user_id, username, full_name, status, checked_in
            FROM rollcalls
            WHERE chat_id = ? AND target_date = ?
        """, (chat_id, target_date)) as cursor:
            return await cursor.fetchall()

async def save_poll_message_id(chat_id: int, target_date: str, message_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO poll_messages (chat_id, target_date, message_id)
            VALUES (?, ?, ?)
            ON CONFLICT(chat_id, target_date) DO UPDATE SET message_id = excluded.message_id
        """, (chat_id, target_date, message_id))
        await db.commit()

async def get_poll_message_id(chat_id: int, target_date: str):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT message_id FROM poll_messages WHERE chat_id = ? AND target_date = ?", (chat_id, target_date)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None

async def get_target_date_by_message_id(chat_id: int, message_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT target_date FROM poll_messages WHERE chat_id = ? AND message_id = ?", (chat_id, message_id)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None

async def get_user_vote(chat_id: int, target_date: str, user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT status FROM rollcalls
            WHERE chat_id = ? AND target_date = ? AND user_id = ?
        """, (chat_id, target_date, user_id)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None

async def remove_vote(chat_id: int, target_date: str, user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            DELETE FROM rollcalls
            WHERE chat_id = ? AND target_date = ? AND user_id = ?
        """, (chat_id, target_date, user_id))
        await db.commit()

async def get_all_dates_for_chat(chat_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT DISTINCT target_date FROM rollcalls
            WHERE chat_id = ?
        """, (chat_id,)) as cursor:
            rows = await cursor.fetchall()
            return [r[0] for r in rows]

