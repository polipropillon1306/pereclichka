import html
from datetime import datetime, timedelta
from typing import List, Tuple
from zoneinfo import ZoneInfo
from config import TIMEZONE

MSK_TZ = ZoneInfo(TIMEZONE)

def get_msk_now() -> datetime:
    """Возвращает текущее время по Москве"""
    return datetime.now(MSK_TZ)

def get_target_date_str(now: datetime = None) -> str:
    """
    Возвращает целевую дату переклички (в формате DD.MM.YYYY).
    Перекличка всегда проводится на следующий день (завтра).
    """
    if now is None:
        now = get_msk_now()
    target = now + timedelta(days=1)
    return target.strftime("%d.%m.%Y")

def get_today_date_str(now: datetime = None) -> str:
    """Возвращает сегодняшнюю дату по Москве в формате DD.MM.YYYY"""
    if now is None:
        now = get_msk_now()
    return now.strftime("%d.%m.%Y")

def get_current_poll_date_str(now: datetime = None) -> str:
    """
    Возвращает дату текущего актуального опроса:
    - С 00:00 до 20:00 актуален опрос на СЕГОДНЯ.
    - С 20:00 до 23:59 актуален опрос на ЗАВТРА.
    """
    if now is None:
        now = get_msk_now()
    if now.hour < 20:
        return get_today_date_str(now)
    return get_target_date_str(now)

def format_poll_text(target_date: str, votes: List[Tuple]) -> str:
    """
    Формирует красивый HTML текст сообщения с перекличкой
    votes: list of (user_id, username, full_name, status, checked_in)
    """
    going = []
    not_going = []

    for user_id, username, full_name, status, checked_in in votes:
        name = f"@{username}" if username else full_name
        safe_name = html.escape(name)
        if status == '+':
            check_mark = " ✅ (в чате)" if checked_in else ""
            going.append(f"• {safe_name}{check_mark}")
        elif status == '-':
            not_going.append(f"• {safe_name}")

    text = f"📋 <b>ПЕРЕКЛИЧКА НА {target_date}</b>\n"
    text += "Кто будет на работе / в офисе?\n\n"

    text += f"✅ <b>Будут ({len(going)}):</b>\n"
    if going:
        text += "\n".join(going) + "\n"
    else:
        text += "<i>Пока никто не отметился</i>\n"

    text += f"\n❌ <b>Не будут ({len(not_going)}):</b>\n"
    if not_going:
        text += "\n".join(not_going) + "\n"
    else:
        text += "<i>Никого</i>\n"

    text += "\nОтвечайте кнопками ниже или отправьте <b>+</b> / <b>-</b> в чат."
    return text
