import html
from datetime import datetime, timedelta
from typing import List, Tuple
from zoneinfo import ZoneInfo
from config import TIMEZONE

MSK_TZ = ZoneInfo(TIMEZONE)

MONTH_NAMES_RU = {
    1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель",
    5: "Май", 6: "Июнь", 7: "Июль", 8: "Август",
    9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь"
}

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

def get_month_sheet_title(date_str: str) -> str:
    """
    Преобразует дату формата 'DD.MM.YYYY' в название листа месяца 'Месяц YYYY'.
    Например: '17.08.2026' -> 'Август 2026'
    """
    try:
        dt = datetime.strptime(date_str.strip(), "%d.%m.%Y")
        month_name = MONTH_NAMES_RU.get(dt.month, f"Месяц {dt.month}")
        return f"{month_name} {dt.year}"
    except Exception:
        return "Посещаемость"

def format_poll_text(target_date: str, votes: List[Tuple]) -> str:
    """
    Формирует красивый HTML текст сообщения с перекличкой
    votes: list of (user_id, username, full_name, status, checked_in, [checkin_time])
    """
    going = []
    not_going = []

    for item in votes:
        user_id = item[0]
        username = item[1]
        full_name = item[2]
        status = item[3]
        checked_in = item[4]
        checkin_time = item[5] if len(item) > 5 else None
        checkout_time = item[6] if len(item) > 6 else None

        name = f"@{username}" if username else full_name
        safe_name = html.escape(name)
        if status == '+':
            if checked_in:
                if checkin_time and checkout_time:
                    check_mark = f" ✅ ({checkin_time} — {checkout_time})"
                elif checkin_time:
                    check_mark = f" ✅ (в чате {checkin_time})"
                elif checkout_time:
                    check_mark = f" ✅ (ушел в {checkout_time})"
                else:
                    check_mark = " ✅"
            else:
                check_mark = ""
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

def format_stats_text(stats_list: list, total_dates: int, period_name: str) -> str:
    """
    Формирует HTML текст для команды /stats
    """
    if not stats_list or total_dates == 0:
        return f"📊 <b>Статистика за {period_name}:</b>\n\n<i>Данных о перекличках за этот период нет.</i>"

    text = f"📊 <b>Статистика посещаемости ({period_name})</b>\n"
    text += f"Всего рабочих смен/перекличек: <b>{total_dates}</b>\n\n"

    for i, s in enumerate(stats_list, start=1):
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
        user_name = f"@{s['username']}" if s['username'] else s['full_name']
        safe_name = html.escape(user_name)

        rel_text = f"<b>{s['reliability']}%</b>" if s["reliability"] is not None else "<i>–</i>"
        text += (
            f"{medal} <b>{safe_name}</b>\n"
            f"   • Вышел на смену: <b>{s['attended']}</b>\n"
            f"   • Ожидался (не подтвердил): <b>{s['expected']}</b>\n"
            f"   • Отказался: <b>{s['not_going']}</b>\n"
            f"   • Пропустил опрос: <b>{s['unmarked']}</b>\n"
            f"   • Надежность выхода: {rel_text}\n\n"
        )

    return text
