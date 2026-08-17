import html
import logging
from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from bot.keyboards import get_rollcall_keyboard
from bot.utils import get_target_date_str, get_today_date_str, format_poll_text
from config import TIMEZONE, ALLOWED_CHAT_IDS
from db import (
    get_all_chats, get_votes_for_date,
    save_poll_message_id, get_poll_message_id
)

logger = logging.getLogger(__name__)

async def send_daily_poll(bot: Bot):
    """Отправка сообщения с перекличкой на следующий день (завтра) в 20:00 по МСК"""
    chats = [c for c in await get_all_chats() if c[0] in ALLOWED_CHAT_IDS]
    target_date = get_target_date_str()

    for chat in chats:
        chat_id = chat[0]
        try:
            # Проверяем, не была ли уже создана перекличка на эту дату (например, вручную через /start_poll)
            existing_poll_id = await get_poll_message_id(chat_id, target_date)
            if existing_poll_id:
                logger.info(f"Перекличка для чата {chat_id} на дату {target_date} уже создана (message_id={existing_poll_id}), пропускаем автоматический опрос.")
                continue

            votes = await get_votes_for_date(chat_id, target_date)
            text = format_poll_text(target_date, votes)

            msg = await bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=get_rollcall_keyboard(),
                parse_mode="HTML"
            )
            await save_poll_message_id(chat_id, target_date, msg.message_id)

            try:
                await msg.pin(disable_notification=True)
            except Exception:
                pass

            logger.info(f"Отправлен ежедневный опрос в чат {chat_id} на дату {target_date}")
        except Exception as e:
            logger.error(f"Ошибка отправки в чат {chat_id}: {e}")

async def check_morning_attendance(bot: Bot):
    """Утренняя проверка в 11:00 по МСК: кто обещался прийти (+), но не написал ни слова с 06:00 до 11:00"""
    chats = [c for c in await get_all_chats() if c[0] in ALLOWED_CHAT_IDS]
    today_date = get_today_date_str()

    for chat in chats:
        chat_id = chat[0]
        try:
            votes = await get_votes_for_date(chat_id, today_date)
            # Фильтруем тех, кто проголосовал '+', но checked_in == 0
            missing_users = [
                f"@{html.escape(username)}" if username else f'<a href="tg://user?id={user_id}">{html.escape(full_name or "Участник")}</a>'
                for user_id, username, full_name, status, checked_in in votes
                if status == '+' and not checked_in
            ]

            if missing_users:
                users_str = ", ".join(missing_users)
                text = (
                    f"⏰ <b>Утренняя проверка!</b>\n\n"
                    f"{users_str}, вы отписались, что будете сегодня, но пока ничего не писали в чат. "
                    f"Вы пришли / будете сегодня?"
                )
                await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
                logger.info(f"Отправлена утренняя проверка в чат {chat_id} для: {users_str}")
        except Exception as e:
            logger.error(f"Ошибка проверки прихода в чате {chat_id}: {e}")

def setup_scheduler(bot: Bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=TIMEZONE)

    # 1. Запуск переклички ежедневно в 20:00 по Москве
    scheduler.add_job(
        send_daily_poll,
        trigger=CronTrigger(hour=20, minute=0, timezone=TIMEZONE),
        args=[bot]
    )

    # 2. Проверка прихода ежедневно в 11:00 по Москве
    scheduler.add_job(
        check_morning_attendance,
        trigger=CronTrigger(hour=11, minute=0, timezone=TIMEZONE),
        args=[bot]
    )

    return scheduler
