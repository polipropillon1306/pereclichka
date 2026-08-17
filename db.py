import aiosqlite
import logging
from config import DB_PATH, ALLOWED_CHAT_IDS

logger = logging.getLogger(__name__)

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA journal_mode = WAL;")
        await db.execute("PRAGMA busy_timeout = 5000;")
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
                checked_in INTEGER DEFAULT 0, -- 1 если подтвердил приход
                checkin_time TEXT, -- HH:MM
                message_id INTEGER,
                UNIQUE(chat_id, target_date, user_id)
            )
        """)
        # Миграция схемы, если таблица уже создана без checkin_time
        try:
            await db.execute("ALTER TABLE rollcalls ADD COLUMN checkin_time TEXT")
        except Exception:
            pass

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

async def save_vote(chat_id: int, target_date: str, user_id: int, username: str, full_name: str, status: str, checkin_time: str = None, checked_in: int = None):
    async with aiosqlite.connect(DB_PATH) as db:
        chk = checked_in
        tm = checkin_time

        await db.execute("""
            INSERT INTO rollcalls (chat_id, target_date, user_id, username, full_name, status, checked_in, checkin_time)
            VALUES (?, ?, ?, ?, ?, ?, COALESCE(?, 0), ?)
            ON CONFLICT(chat_id, target_date, user_id) DO UPDATE SET
                status = excluded.status,
                username = excluded.username,
                full_name = excluded.full_name,
                checked_in = CASE 
                    WHEN ? IS NOT NULL THEN ?
                    WHEN excluded.status = '+' THEN checked_in 
                    ELSE 0 
                END,
                checkin_time = CASE 
                    WHEN ? IS NOT NULL THEN ?
                    WHEN excluded.status = '+' THEN checkin_time 
                    ELSE NULL 
                END
        """, (chat_id, target_date, user_id, username, full_name, status, chk, tm, chk, chk, tm, tm))
        await db.commit()

async def set_checked_in(chat_id: int, target_date: str, user_id: int, checkin_time: str = None) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            UPDATE rollcalls SET checked_in = 1, checkin_time = ?
            WHERE chat_id = ? AND target_date = ? AND user_id = ? AND status = '+' AND checked_in = 0
        """, (checkin_time, chat_id, target_date, user_id))
        await db.commit()
        return cursor.rowcount > 0

async def get_votes_for_date(chat_id: int, target_date: str):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT user_id, username, full_name, status, checked_in, checkin_time
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

async def find_user_by_identifier(chat_id: int, identifier: str):
    """Ищет пользователя в истории перекличек чата по @username или имени"""
    clean_id = identifier.strip().lstrip('@').lower()
    async with aiosqlite.connect(DB_PATH) as db:
        # Поиск по username
        async with db.execute("""
            SELECT user_id, username, full_name FROM rollcalls
            WHERE chat_id = ? AND LOWER(username) = ?
            ORDER BY id DESC LIMIT 1
        """, (chat_id, clean_id)) as cursor:
            row = await cursor.fetchone()
            if row:
                return {"user_id": row[0], "username": row[1], "full_name": row[2]}

        # Поиск по частичному совпадению full_name
        async with db.execute("""
            SELECT user_id, username, full_name FROM rollcalls
            WHERE chat_id = ? AND LOWER(full_name) LIKE ?
            ORDER BY id DESC LIMIT 1
        """, (chat_id, f"%{clean_id}%")) as cursor:
            row = await cursor.fetchone()
            if row:
                return {"user_id": row[0], "username": row[1], "full_name": row[2]}

    return None

async def get_attendance_stats(chat_id: int, month_year: str = None):
    """
    Агрегирует статистику по участникам за месяц (например '08.2026') или все время.
    Возвращает: (stats_list, total_dates_count)
    """
    async with aiosqlite.connect(DB_PATH) as db:
        query = """
            SELECT user_id, username, full_name, status, checked_in, target_date
            FROM rollcalls
            WHERE chat_id = ?
        """
        params = [chat_id]
        if month_year:
            query += " AND target_date LIKE ?"
            params.append(f"%.{month_year}")

        async with db.execute(query, tuple(params)) as cursor:
            rows = await cursor.fetchall()

        dates_query = "SELECT DISTINCT target_date FROM rollcalls WHERE chat_id = ?"
        dates_params = [chat_id]
        if month_year:
            dates_query += " AND target_date LIKE ?"
            dates_params.append(f"%.{month_year}")

        async with db.execute(dates_query, tuple(dates_params)) as cursor:
            all_dates = [r[0] for r in await cursor.fetchall()]

        total_dates_count = len(all_dates)

        user_stats = {}
        for uid, uname, fname, status, chk_in, t_date in rows:
            if uid not in user_stats:
                user_stats[uid] = {
                    "user_id": uid,
                    "username": uname,
                    "full_name": fname,
                    "attended": 0,    # + (Пришел)
                    "expected": 0,    # + (Ожидается)
                    "not_going": 0,   # - (Не будет)
                    "total_votes": 0,
                    "total_polls": total_dates_count
                }
            if uname:
                user_stats[uid]["username"] = uname
            if fname:
                user_stats[uid]["full_name"] = fname

            user_stats[uid]["total_votes"] += 1
            if status == '+':
                if chk_in:
                    user_stats[uid]["attended"] += 1
                else:
                    user_stats[uid]["expected"] += 1
            elif status == '-':
                user_stats[uid]["not_going"] += 1

        stats_list = list(user_stats.values())
        for s in stats_list:
            planned = s["attended"] + s["expected"]
            s["reliability"] = round((s["attended"] / planned * 100)) if planned > 0 else 0
            s["unmarked"] = max(0, total_dates_count - s["total_votes"])

        stats_list.sort(key=lambda x: (x["attended"], x["reliability"]), reverse=True)
        return stats_list, total_dates_count

async def get_all_known_users_for_chat(chat_id: int):
    """Возвращает список всех известных участников чата (user_id, username, full_name)"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT user_id, username, full_name, MAX(id) as max_id
            FROM rollcalls
            WHERE chat_id = ?
            GROUP BY user_id
            ORDER BY max_id ASC
        """, (chat_id,)) as cursor:
            rows = await cursor.fetchall()
            return [(r[0], r[1], r[2]) for r in rows]
