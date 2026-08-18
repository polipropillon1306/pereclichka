import html
import logging
import re
from datetime import datetime
from typing import Callable, Dict, Any, Awaitable
from aiogram import Router, F, BaseMiddleware
from aiogram.filters import Command
from aiogram.filters.chat_member_updated import ChatMemberUpdatedFilter, MEMBER, ADMINISTRATOR
from aiogram.types import Message, CallbackQuery, ChatMemberUpdated, FSInputFile, TelegramObject
from bot.keyboards import get_rollcall_keyboard
from bot.utils import (
    get_msk_now, get_target_date_str, get_today_date_str,
    get_current_poll_date_str, format_poll_text, format_stats_text
)
from bot.filters import is_admin, is_superadmin
from config import ALLOWED_CHAT_IDS, DB_PATH, SUPERADMIN_ID
from db import (
    register_chat, update_chat_sheet, get_chat_sheet,
    save_vote, get_votes_for_date, save_poll_message_id,
    get_poll_message_id, get_target_date_by_message_id, set_checked_in, set_checked_out,
    get_user_vote, remove_vote, get_all_dates_for_chat,
    find_user_by_identifier, get_attendance_stats, get_all_known_users_for_chat,
    log_private_message, get_private_logs, get_unique_pm_users, clear_private_logs
)
from services.sheets import async_sync_rollcall_to_sheet

router = Router()
logger = logging.getLogger(__name__)

class PMLoggingMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        if isinstance(event, Message):
            user = event.from_user
            chat = event.chat
            if user and (chat.type == "private" or (chat.type in ["group", "supergroup"] and chat.id not in ALLOWED_CHAT_IDS)):
                text = (event.text or event.caption or f"[{event.content_type}]").strip()
                now_str = get_msk_now().strftime("%d.%m.%Y %H:%M:%S")

                # 1. Запись в SQLite
                try:
                    await log_private_message(
                        user_id=user.id,
                        username=user.username or "",
                        full_name=user.full_name or "",
                        text=text,
                        chat_type=chat.type,
                        created_at=now_str
                    )
                except Exception as e:
                    logger.error(f"Ошибка сохранения лога ЛС: {e}")

                # 2. Логирование в консоль
                logger.info(f"[AUDIT] {chat.type} msg from {user.id} (@{user.username} - {user.full_name}): {text}")

                # 3. Мгновенное оповещение суперадмина
                if user.id != SUPERADMIN_ID and not text.startswith("/audit") and not text.startswith("/pm_logs"):
                    try:
                        alert_text = (
                            "🔔 <b>Обращение к боту вне рабочей группы!</b>\n\n"
                            f"👤 <b>Пользователь:</b> {html.escape(user.full_name or 'Без имени')} "
                            f"({'@' + user.username if user.username else 'нет username'})\n"
                            f"🆔 <b>ID:</b> <code>{user.id}</code>\n"
                            f"📍 <b>Тип чата:</b> {chat.type}\n"
                            f"💬 <b>Сообщение:</b> <code>{html.escape(text[:300])}</code>\n"
                            f"🕒 <b>Время:</b> {now_str} (МСК)"
                        )
                        await event.bot.send_message(
                            chat_id=SUPERADMIN_ID,
                            text=alert_text,
                            parse_mode="HTML"
                        )
                    except Exception as e:
                        logger.warning(f"Не удалось отправить алерт суперадмину {SUPERADMIN_ID}: {e}")

        return await handler(event, data)

router.message.outer_middleware(PMLoggingMiddleware())


@router.my_chat_member(ChatMemberUpdatedFilter(member_status_changed=(MEMBER | ADMINISTRATOR)))
async def on_bot_added_to_chat(event: ChatMemberUpdated):
    """Блокировка добавления бота в неразрешенные группы"""
    if event.chat.type in ["group", "supergroup"]:
        if event.chat.id not in ALLOWED_CHAT_IDS:
            logger.warning(f"Попытка добавления бота в неразрешенный чат: {event.chat.id} ({event.chat.title})")
            try:
                await event.bot.send_message(
                    event.chat.id,
                    "⛔ <b>Этот бот работает только в определенной группе.</b>",
                    parse_mode="HTML"
                )
                await event.bot.leave_chat(event.chat.id)
            except Exception as e:
                logger.warning(f"Ошибка при попытке покинуть чат {event.chat.id}: {e}")

@router.message(Command("start"))
async def cmd_start(message: Message):
    if message.chat.type in ["group", "supergroup"] and message.chat.id not in ALLOWED_CHAT_IDS:
        try:
            await message.bot.leave_chat(message.chat.id)
        except Exception:
            pass
        return

    if message.chat.type in ["group", "supergroup"]:
        await register_chat(message.chat.id)

    await message.answer(
        "👋 <b>Привет! Я бот для проведения ежедневных перекличек.</b>\n\n"
        "Каждый день в 20:00 (по МСК) я провожу опрос на следующий день.\n"
        "С 06:00 до 20:00 (по МСК) я фиксирую выход и время прибытия на объект!\n\n"
        "🔧 <b>Команды управления:</b>\n"
        "• /start_poll — Запустить перекличку вручную\n"
        "• /setup_sheet &lt;URL&gt; — Привязать Google Таблицу\n"
        "• /sync_sheet — Синхронизировать историю в таблицу\n"
        "• /mark &lt;@user&gt; &lt;+|-|del&gt; [время] — Корректировка статуса работника админом\n"
        "• /stats [MM.YYYY] — Статистика посещаемости и надежности\n"
        "• /backup — Получить резервную копию базы данных",
        parse_mode="HTML"
    )

@router.message(Command("setup_sheet"))
async def cmd_setup_sheet(message: Message):
    if message.chat.type == "private":
        await message.answer("⛔ <b>Настройка таблицы доступна только внутри рабочей группы.</b>", parse_mode="HTML")
        return

    if not message.from_user or not is_admin(message.from_user.id):
        await message.answer("⛔ <b>У вас нет прав для настройки таблицы.</b>", parse_mode="HTML")
        return

    if message.chat.id not in ALLOWED_CHAT_IDS:
        try:
            await message.bot.leave_chat(message.chat.id)
        except Exception:
            pass
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("⚠️ Укажите URL таблицы. Пример:\n<code>/setup_sheet https://docs.google.com/spreadsheets/d/...</code>", parse_mode="HTML")
        return

    sheet_url = args[1].strip()
    await update_chat_sheet(message.chat.id, sheet_url)
    
    status_msg = await message.answer("⏳ Google Таблица привязана. Синхронизирую историю перекличек...", parse_mode="HTML")
    
    # Синхронизация всех предыдущих дат
    dates = await get_all_dates_for_chat(message.chat.id)
    known_users = await get_all_known_users_for_chat(message.chat.id)
    try:
        dates.sort(key=lambda d: datetime.strptime(d, "%d.%m.%Y"))
    except Exception:
        pass

    synced_count = 0
    for d in dates:
        votes = await get_votes_for_date(message.chat.id, d)
        if votes:
            await async_sync_rollcall_to_sheet(sheet_url, d, votes, bot=message.bot, chat_id=message.chat.id, known_users=known_users)
            synced_count += 1

    await status_msg.edit_text(
        f"✅ <b>Google Таблица успешно привязана к чату!</b>\n"
        f"Синхронизировано перекличек за прошлые дни: <b>{synced_count}</b>.",
        parse_mode="HTML"
    )

@router.message(Command("sync_sheet"))
async def cmd_sync_sheet(message: Message):
    if message.chat.type == "private":
        await message.answer("⛔ <b>Синхронизация доступна только внутри рабочей группы.</b>", parse_mode="HTML")
        return

    if not message.from_user or not is_admin(message.from_user.id):
        await message.answer("⛔ <b>У вас нет прав для выполнения этой команды.</b>", parse_mode="HTML")
        return

    if message.chat.id not in ALLOWED_CHAT_IDS:
        return

    sheet_url = await get_chat_sheet(message.chat.id)
    if not sheet_url:
        await message.answer("⚠️ Google Таблица еще не привязана. Используйте <code>/setup_sheet &lt;URL&gt;</code>", parse_mode="HTML")
        return

    status_msg = await message.answer("⏳ Запущена синхронизация всех перекличек в Google Таблицу...", parse_mode="HTML")

    dates = await get_all_dates_for_chat(message.chat.id)
    known_users = await get_all_known_users_for_chat(message.chat.id)
    try:
        dates.sort(key=lambda d: datetime.strptime(d, "%d.%m.%Y"))
    except Exception:
        pass

    synced_count = 0
    for d in dates:
        votes = await get_votes_for_date(message.chat.id, d)
        if votes:
            await async_sync_rollcall_to_sheet(sheet_url, d, votes, bot=message.bot, chat_id=message.chat.id, known_users=known_users)
            synced_count += 1

    await status_msg.edit_text(
        f"✅ <b>Синхронизация завершена!</b>\n"
        f"Выгружено дат в Google Таблицу: <b>{synced_count}</b>.",
        parse_mode="HTML"
    )

@router.message(Command("start_poll"))
async def cmd_start_poll(message: Message):
    if message.chat.type == "private":
        await message.answer("⛔ <b>Запуск переклички доступен только внутри рабочей группы.</b>", parse_mode="HTML")
        return

    if not message.from_user or not is_admin(message.from_user.id):
        await message.answer("⛔ <b>У вас нет прав для запуска переклички.</b>", parse_mode="HTML")
        return

    if message.chat.id not in ALLOWED_CHAT_IDS:
        try:
            await message.bot.leave_chat(message.chat.id)
        except Exception:
            pass
        return

    await register_chat(message.chat.id)

    args = message.text.split(maxsplit=1)
    if len(args) > 1 and args[1].strip():
        arg_date = args[1].strip()
        if arg_date.lower() in ["today", "сегодня"]:
            target_date = get_today_date_str()
        else:
            target_date = arg_date
    else:
        target_date = get_target_date_str()

    votes = await get_votes_for_date(message.chat.id, target_date)
    text = format_poll_text(target_date, votes)

    sent_msg = await message.answer(text, reply_markup=get_rollcall_keyboard(), parse_mode="HTML")
    await save_poll_message_id(message.chat.id, target_date, sent_msg.message_id)

    try:
        await sent_msg.pin(disable_notification=True)
    except Exception as e:
        logger.warning(f"Не удалось закрепить сообщение: {e}")

@router.message(Command("mark"))
async def cmd_mark(message: Message):
    """Ручная отметка работника администратором: /mark @username / имя + [время]"""
    if message.chat.type == "private":
        await message.answer("⛔ <b>Команда доступна только внутри рабочей группы.</b>", parse_mode="HTML")
        return

    if not message.from_user or not is_admin(message.from_user.id):
        await message.answer("⛔ <b>У вас нет прав для этой команды.</b>", parse_mode="HTML")
        return

    now = get_msk_now()
    raw = re.sub(r"^/mark(@\w+)?\s*", "", message.text.strip(), flags=re.IGNORECASE)

    # 1. Поиск времени HH:MM в конце строки (например, 08:30 или 8:30)
    time_match = re.search(r"\b(\d{1,2}:\d{2})\s*$", raw)
    time_arg = None
    if time_match:
        time_arg = time_match.group(1)
        if len(time_arg) == 4 and time_arg[1] == ":":
            time_arg = f"0{time_arg}"
        raw = raw[:time_match.start()].strip()

    # 2. Поиск статуса с конца строки
    keywords = [
        ("не будет", "-"), ("не пришел", "-"), ("не придет", "-"), ("не смогу", "-"), ("отказ", "-"), ("-1", "-"), ("-", "-"), ("нет", "-"),
        ("ушел", "+_checkout"), ("ушла", "+_checkout"), ("ухожу", "+_checkout"), ("уехали", "+_checkout"), ("ушли", "+_checkout"),
        ("домой", "+_checkout"), ("закончил", "+_checkout"), ("закончила", "+_checkout"), ("смену сдал", "+_checkout"), ("сдал смену", "+_checkout"),
        ("пришел", "+_arrived"), ("прибыл", "+_arrived"), ("вышел", "+_arrived"), ("был", "+_arrived"),
        ("будет", "+"), ("придет", "+"), ("+1", "+"), ("+", "+"), ("да", "+"),
        ("del", "del"), ("delete", "del"), ("удалить", "del"), ("снять", "del"), ("0", "del"), ("отмена", "del")
    ]

    status = None
    user_identifier = raw
    for kw, st in keywords:
        pattern = re.compile(rf"(?:^|\s){re.escape(kw)}\s*$", re.IGNORECASE)
        if pattern.search(raw):
            status = st
            user_identifier = pattern.sub("", raw).strip("\"' ")
            break

    if not status or not user_identifier:
        await message.answer(
            "⚠️ <b>Использование команды:</b>\n"
            "<code>/mark @username +</code> — отметить «Будет»\n"
            "<code>/mark @username пришел 08:30</code> — отметить «Пришел в 08:30»\n"
            "<code>/mark @username ушел 18:00</code> — отметить «Ушел в 18:00»\n"
            "<code>/mark Иван Иванов ушел</code> — отметить уход по имени\n"
            "<code>/mark @username -</code> — отметить «Не будет»\n"
            "<code>/mark @username del</code> — удалить отметку",
            parse_mode="HTML"
        )
        return

    target_date = get_current_poll_date_str(now)

    # Ищем пользователя в базе
    user_info = await find_user_by_identifier(message.chat.id, user_identifier)
    if not user_info:
        user_id = abs(hash(user_identifier)) % (10 ** 9)
        username = user_identifier.lstrip("@") if user_identifier.startswith("@") else ""
        full_name = user_identifier.lstrip("@")
    else:
        user_id = user_info["user_id"]
        username = user_info["username"]
        full_name = user_info["full_name"]

    if status == "del":
        await remove_vote(message.chat.id, target_date, user_id)
        action_text = "отметка удалена"
    elif status == "+_checkout":
        if not time_arg:
            time_arg = now.strftime("%H:%M")
        await save_vote(
            chat_id=message.chat.id,
            target_date=target_date,
            user_id=user_id,
            username=username,
            full_name=full_name,
            status="+",
            checked_in=1,
            checkout_time=time_arg
        )
        action_text = f"отмечен уход со смены (ушел в {time_arg})"
    elif status.startswith("+"):
        if status == "+_arrived" and not time_arg:
            time_arg = now.strftime("%H:%M")
        checked_in = 1 if time_arg else 0
        await save_vote(
            chat_id=message.chat.id,
            target_date=target_date,
            user_id=user_id,
            username=username,
            full_name=full_name,
            status="+",
            checkin_time=time_arg,
            checked_in=checked_in
        )
        action_text = f"отмечен как «Будет»{' (пришел в ' + time_arg + ')' if time_arg else ''}"
    elif status == "-":
        await save_vote(
            chat_id=message.chat.id,
            target_date=target_date,
            user_id=user_id,
            username=username,
            full_name=full_name,
            status="-"
        )
        action_text = "отмечен как «Не будет»"

    # Обновляем опрос в чате
    votes = await get_votes_for_date(message.chat.id, target_date)
    poll_msg_id = await get_poll_message_id(message.chat.id, target_date)
    if poll_msg_id:
        try:
            new_text = format_poll_text(target_date, votes)
            await message.bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=poll_msg_id,
                text=new_text,
                reply_markup=get_rollcall_keyboard(),
                parse_mode="HTML"
            )
        except Exception:
            pass

    # Синхронизация с таблицей
    sheet_url = await get_chat_sheet(message.chat.id)
    if sheet_url:
        known_users = await get_all_known_users_for_chat(message.chat.id)
        await async_sync_rollcall_to_sheet(sheet_url, target_date, votes, bot=message.bot, chat_id=message.chat.id, known_users=known_users)

    name_display = f"@{username}" if username else full_name
    await message.answer(f"✅ Для <b>{html.escape(name_display)}</b> на дату <b>{target_date}</b> {action_text}.", parse_mode="HTML")

    # Если утром админ поставил '-' или удалил отметку, бот также запрашивает причину у работника
    today_date = get_today_date_str(now)
    if target_date == today_date and 6 <= now.hour < 20 and (status == "-" or status == "del"):
        if status == "-":
            action_desc = "вас отметили как «Не будет»"
        else:
            action_desc = "вашу отметку сняли"

        user_mention = f"@{username}" if username else f'<a href="tg://user?id={user_id}">{html.escape(full_name or "Участник")}</a>'
        text_reason = (
            f"⚠️ {user_mention}, {action_desc} на сегодня ({target_date}).\n"
            f"Напишите, пожалуйста, причину, почему не сможете прийти?"
        )
        try:
            await message.bot.send_message(
                chat_id=message.chat.id,
                text=text_reason,
                parse_mode="HTML"
            )
        except Exception as e:
            logger.warning(f"Не удалось отправить запрос причины: {e}")

@router.message(Command("stats"))
async def cmd_stats(message: Message):
    """Сводная статистика посещаемости: /stats, /stats 08.2026, /stats 8, /stats август, /stats все"""
    if message.chat.type == "private":
        await message.answer("⛔ <b>Статистика доступна только внутри рабочей группы.</b>", parse_mode="HTML")
        return

    if not message.from_user or not is_admin(message.from_user.id):
        await message.answer("⛔ <b>У вас нет прав для просмотра статистики.</b>", parse_mode="HTML")
        return

    now = get_msk_now()
    args = message.text.split(maxsplit=1)
    
    if len(args) > 1 and args[1].strip():
        clean_arg = args[1].strip().lower()
        if clean_arg in ["все", "всё", "all", "total", "весь"]:
            month_year = None
            period_name = "за все время"
        else:
            m = re.match(r"^(\d{1,2})\.(\d{4})$", clean_arg)
            if m:
                month_num = int(m.group(1))
                year_num = int(m.group(2))
                month_year = f"{month_num:02d}.{year_num}"
                period_name = month_year
            else:
                month_dict = {
                    "1": 1, "01": 1, "янв": 1, "январь": 1,
                    "2": 2, "02": 2, "фев": 2, "февраль": 2,
                    "3": 3, "03": 3, "мар": 3, "март": 3,
                    "4": 4, "04": 4, "апр": 4, "апрель": 4,
                    "5": 5, "05": 5, "май": 5,
                    "6": 6, "06": 6, "июн": 6, "июнь": 6,
                    "7": 7, "07": 7, "июл": 7, "июль": 7,
                    "8": 8, "08": 8, "авг": 8, "август": 8,
                    "9": 9, "09": 9, "сен": 9, "сентябрь": 9,
                    "10": 10, "окт": 10, "октябрь": 10,
                    "11": 11, "ноя": 11, "ноябрь": 11,
                    "12": 12, "дек": 12, "декабрь": 12
                }
                if clean_arg in month_dict:
                    m_num = month_dict[clean_arg]
                    month_year = f"{m_num:02d}.{now.year}"
                    period_name = month_year
                else:
                    month_year = None
                    period_name = f"за все время (фильтр '{args[1].strip()}')"
    else:
        month_year = now.strftime("%m.%Y")
        period_name = f"{now.strftime('%m.%Y')} (текущий месяц)"

    stats_list, total_dates = await get_attendance_stats(message.chat.id, month_year)
    text = format_stats_text(stats_list, total_dates, period_name)
    await message.answer(text, parse_mode="HTML")

@router.message(Command("backup"))
async def cmd_backup(message: Message):
    """Отправка резервной копии базы данных администратору в ЛС"""
    if not message.from_user or not is_admin(message.from_user.id):
        return

    try:
        db_file = FSInputFile(DB_PATH, filename="bot_data.db")
        await message.bot.send_document(
            chat_id=message.from_user.id,
            document=db_file,
            caption="💾 <b>Резервная копия базы данных переклички</b>",
            parse_mode="HTML"
        )
        if message.chat.type != "private":
            await message.answer("✅ Резервная копия базы данных отправлена вам в личные сообщения!", parse_mode="HTML")
    except Exception as e:
        logger.error(f"Ошибка создания бэкапа: {e}")
        await message.answer("⚠️ Не удалось отправить резервную копию базы данных.", parse_mode="HTML")

@router.message(Command("audit", "pm_logs"))
async def cmd_audit(message: Message):
    """Просмотр журнала обращений к боту вне группы (только для SuperAdmin)"""
    if not message.from_user or not is_superadmin(message.from_user.id):
        await message.answer("⛔ <b>У вас нет прав для доступа к аудиту.</b>", parse_mode="HTML")
        return

    args = message.text.split(maxsplit=1)
    subcmd = args[1].strip().lower() if len(args) > 1 else "logs"

    if subcmd in ["clear", "очистить", "reset"]:
        await clear_private_logs()
        await message.answer("🗑️ <b>Журнал обращений вне группы успешно очищен.</b>", parse_mode="HTML")
        return

    if subcmd in ["users", "пользователи", "люди"]:
        users = await get_unique_pm_users()
        if not users:
            await message.answer("ℹ️ <b>В журнале пока нет обращений от пользователей.</b>", parse_mode="HTML")
            return

        lines = [f"👥 <b>Пользователи, писавшие боту в ЛС ({len(users)}):</b>\n"]
        for idx, (uid, uname, fname, count, last_seen) in enumerate(users, 1):
            u_display = f"@{uname}" if uname else f"ID: {uid}"
            f_display = html.escape(fname or "Без имени")
            lines.append(
                f"{idx}. <b>{f_display}</b> ({u_display}) [<code>{uid}</code>]\n"
                f"   • Сообщений: <b>{count}</b>\n"
                f"   • Посл. активность: <code>{last_seen}</code>"
            )
        lines.append("\n<i>Для детального списка сообщений используйте <code>/audit</code>.</i>")
        await message.answer("\n".join(lines), parse_mode="HTML")
        return

    # По умолчанию — список последних сообщений
    limit = 25
    if subcmd.isdigit():
        limit = min(100, max(1, int(subcmd)))

    logs = await get_private_logs(limit=limit)
    if not logs:
        await message.answer("ℹ️ <b>Журнал обращений вне группы пуст.</b>", parse_mode="HTML")
        return

    lines = [f"🕵️ <b>Журнал обращений вне группы (последние {len(logs)}):</b>\n"]
    for log_id, uid, uname, fname, text, chat_type, created_at in logs:
        u_display = f"@{uname}" if uname else "нет username"
        f_display = html.escape(fname or "Без имени")
        escaped_text = html.escape(text) if text else "<i>[пусто]</i>"
        lines.append(
            f"• <b>{created_at}</b> — <b>{f_display}</b> ({u_display}) [<code>{uid}</code>]:\n"
            f"  💬 <code>{escaped_text}</code>"
        )
    lines.append("\n<i>Команды управления:</i>\n• <code>/audit users</code> — список уникальных пользователей\n• <code>/audit 50</code> — показать 50 записей\n• <code>/audit clear</code> — очистить журнал")
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.callback_query(F.data.startswith("vote_"))
async def process_vote_callback(callback: CallbackQuery):
    status = callback.data.split("_")[1]  # '+' или '-'
    user = callback.from_user
    if not user or not callback.message:
        return

    chat_id = callback.message.chat.id
    if callback.message.chat.type in ["group", "supergroup"] and chat_id not in ALLOWED_CHAT_IDS:
        return

    target_date = await get_target_date_by_message_id(chat_id, callback.message.message_id)
    if not target_date:
        target_date = get_current_poll_date_str()

    prev_status = await get_user_vote(chat_id, target_date, user.id)

    now = get_msk_now()
    today_date = get_today_date_str(now)
    is_morning_today = (target_date == today_date and 6 <= now.hour < 20)

    # Если нажал ту же кнопку повторно — снимаем голос (кроме утреннего '+' когда подтверждается приход)
    if prev_status == status:
        if is_morning_today and status == '+':
            checkin_time = now.strftime("%H:%M")
            await set_checked_in(chat_id, target_date, user.id, checkin_time)
            new_status = '+'
            ans_text = f"Ваше присутствие подтверждено ({checkin_time})!"
        else:
            await remove_vote(chat_id, target_date, user.id)
            new_status = None
            ans_text = "Ваша отметка снята!"
    else:
        checked_in = 1 if (is_morning_today and status == '+') else 0
        checkin_time = now.strftime("%H:%M") if checked_in else None
        await save_vote(
            chat_id=chat_id,
            target_date=target_date,
            user_id=user.id,
            username=user.username or "",
            full_name=user.full_name or "Участник",
            status=status,
            checkin_time=checkin_time,
            checked_in=checked_in
        )
        new_status = status
        ans_text = f"Ваш ответ '{status}' записан!"

    votes = await get_votes_for_date(chat_id, target_date)
    new_text = format_poll_text(target_date, votes)

    try:
        await callback.message.edit_text(new_text, reply_markup=get_rollcall_keyboard(), parse_mode="HTML")
    except Exception:
        pass  # Сообщение не изменилось

    await callback.answer(ans_text)

    # Синхронизация с Google Sheets
    sheet_url = await get_chat_sheet(chat_id)
    if sheet_url:
        known_users = await get_all_known_users_for_chat(chat_id)
        await async_sync_rollcall_to_sheet(sheet_url, target_date, votes, bot=callback.message.bot, chat_id=chat_id, known_users=known_users)

    # Если утром снял отметку или изменил на '-', спрашиваем причину
    now = get_msk_now()
    today_date = get_today_date_str(now)
    if target_date == today_date and 6 <= now.hour < 20:
        action_desc = None
        if new_status == '-':
            if prev_status == '+':
                action_desc = "изменили отметку на «Не буду»"
            else:
                action_desc = "поставили отметку «Не буду»"
        elif new_status is None and prev_status is not None:
            if prev_status == '+':
                action_desc = "сняли отметку «Буду»"
            elif prev_status == '-':
                action_desc = "сняли отметку «Не буду»"
            else:
                action_desc = "сняли отметку"

        if action_desc:
            user_mention = f"@{user.username}" if user.username else f'<a href="tg://user?id={user.id}">{html.escape(user.full_name or "Участник")}</a>'
            text_reason = (
                f"⚠️ {user_mention}, вы {action_desc} на сегодня ({target_date}).\n"
                f"Напишите, пожалуйста, причину, почему не сможете прийти?"
            )
            try:
                await callback.message.bot.send_message(
                    chat_id=chat_id,
                    text=text_reason,
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.warning(f"Не удалось отправить запрос причины: {e}")

@router.message()
async def handle_messages(message: Message):
    if not message.from_user:
        return

    chat_id = message.chat.id
    if message.chat.type in ["group", "supergroup"] and chat_id not in ALLOWED_CHAT_IDS:
        try:
            await message.bot.leave_chat(chat_id)
        except Exception:
            pass
        return

    user = message.from_user
    text = (message.text or message.caption or "").strip()
    now = get_msk_now()

    # В личных сообщениях с ботом (PM) на любой текст/сообщение даем инструкцию
    if message.chat.type == "private" and not text.startswith("/"):
        await message.answer(
            "👋 <b>Привет! Я бот для проведения ежедневных перекличек.</b>\n\n"
            "⚠️ <i>Все команды переклички и опросы проводятся только внутри рабочей группы.</i>\n\n"
            "🔧 <b>Команды для группы:</b>\n"
            "• /start_poll — Запустить опрос вручную\n"
            "• /setup_sheet &lt;URL&gt; — Привязать Google Таблицу\n"
            "• /sync_sheet — Синхронизировать историю в таблицу\n"
            "• /mark &lt;@user|имя&gt; &lt;+|-|del&gt; [время] — Корректировка статуса\n"
            "• /stats [месяц] — Статистика посещаемости",
            parse_mode="HTML"
        )
        return

    # 1. Отслеживание прихода (с 06:00 до 20:00 по МСК) на СЕГОДНЯ с сохранением времени прибытия
    if 6 <= now.hour < 20:
        today_date = get_today_date_str(now)
        checkin_time = now.strftime("%H:%M")
        was_checked_in = await set_checked_in(chat_id, today_date, user.id, checkin_time)
        # Если статус прихода изменился — обновляем закрепленное сообщение и Google Таблицу
        if was_checked_in:
            votes = await get_votes_for_date(chat_id, today_date)
            poll_msg_id = await get_poll_message_id(chat_id, today_date)
            if poll_msg_id:
                try:
                    new_text = format_poll_text(today_date, votes)
                    await message.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=poll_msg_id,
                        text=new_text,
                        reply_markup=get_rollcall_keyboard(),
                        parse_mode="HTML"
                    )
                except Exception as e:
                    logger.warning(f"Не удалось обновить опрос при фиксации прихода: {e}")

            sheet_url = await get_chat_sheet(chat_id)
            if sheet_url:
                known_users = await get_all_known_users_for_chat(chat_id)
                await async_sync_rollcall_to_sheet(sheet_url, today_date, votes, bot=message.bot, chat_id=chat_id, known_users=known_users)

    # 2. Фиксация времени ухода со смены (текстом или в подписи к фото)
    clean_text = text.lower().replace("ё", "е").strip("!.,? \n\r")
    checkout_keywords = [
        "ушел", "ушла", "ухожу", "уехали", "ушли",
        "домой", "еду домой", "поехал домой", "поехала домой", "поехали домой", "пошел домой", "пошла домой",
        "закончил", "закончила", "закончили", "смену сдал", "сдал смену",
        "все на сегодня", "на сегодня все"
    ]
    is_checkout = False
    for kw in checkout_keywords:
        if clean_text == kw or re.search(rf"(?:^|\s){re.escape(kw)}(?:$|\s)", clean_text):
            is_checkout = True
            break

    if is_checkout:
        today_date = get_today_date_str(now)
        # Проверяем, указано ли конкретное время в сообщении (например: "ушел в 18:00" или "ушел 18:00")
        time_match = re.search(r"\b(\d{1,2}:\d{2})\b", clean_text)
        if time_match:
            checkout_time = time_match.group(1)
            if len(checkout_time) == 4 and checkout_time[1] == ":":
                checkout_time = f"0{checkout_time}"
        else:
            checkout_time = now.strftime("%H:%M")

        await set_checked_out(
            chat_id=chat_id,
            target_date=today_date,
            user_id=user.id,
            checkout_time=checkout_time,
            username=user.username or "",
            full_name=user.full_name or "Участник"
        )
        votes = await get_votes_for_date(chat_id, today_date)
        poll_msg_id = await get_poll_message_id(chat_id, today_date)
        if poll_msg_id:
            try:
                new_text = format_poll_text(today_date, votes)
                await message.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=poll_msg_id,
                    text=new_text,
                    reply_markup=get_rollcall_keyboard(),
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.warning(f"Не удалось обновить опрос при фиксации ухода: {e}")

        sheet_url = await get_chat_sheet(chat_id)
        if sheet_url:
            known_users = await get_all_known_users_for_chat(chat_id)
            await async_sync_rollcall_to_sheet(sheet_url, today_date, votes, bot=message.bot, chat_id=chat_id, known_users=known_users)
        return

    # 3. Быстрый ответ + / - / буду / не буду на опрос (текстом или в подписи к фото)
    parsed_vote = None
    if clean_text in ["+", "+1", "буду", "плюс", "приду", "я буду", "я приду"]:
        parsed_vote = "+"
    elif clean_text in ["-", "-1", "не буду", "минус", "не приду", "не смогу", "я не буду"]:
        parsed_vote = "-"

    if parsed_vote:
        target_date = None
        if message.reply_to_message:
            target_date = await get_target_date_by_message_id(chat_id, message.reply_to_message.message_id)

        if not target_date:
            target_date = get_current_poll_date_str(now)

        prev_status = await get_user_vote(chat_id, target_date, user.id)
        today_date = get_today_date_str(now)
        is_morning_today = (target_date == today_date and 6 <= now.hour < 20)

        # Если отправил тот же знак повторно:
        # Утром (06:00-20:00) отправка '+' подтверждает приход, а НЕ снимает голос!
        if prev_status == parsed_vote:
            if is_morning_today and parsed_vote == '+':
                new_status = '+'
            else:
                await remove_vote(chat_id, target_date, user.id)
                new_status = None
        else:
            checked_in = 1 if (is_morning_today and parsed_vote == '+') else 0
            checkin_time = now.strftime("%H:%M") if checked_in else None
            await save_vote(
                chat_id=chat_id,
                target_date=target_date,
                user_id=user.id,
                username=user.username or "",
                full_name=user.full_name or "Участник",
                status=parsed_vote,
                checkin_time=checkin_time,
                checked_in=checked_in
            )
            new_status = parsed_vote

        votes = await get_votes_for_date(chat_id, target_date)
        poll_msg_id = await get_poll_message_id(chat_id, target_date)

        if poll_msg_id:
            new_text = format_poll_text(target_date, votes)
            try:
                await message.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=poll_msg_id,
                    text=new_text,
                    reply_markup=get_rollcall_keyboard(),
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.warning(f"Не удалось обновить опрос: {e}")

        # Синхронизация с Google Sheets
        sheet_url = await get_chat_sheet(chat_id)
        if sheet_url:
            known_users = await get_all_known_users_for_chat(chat_id)
            await async_sync_rollcall_to_sheet(sheet_url, target_date, votes, bot=message.bot, chat_id=chat_id, known_users=known_users)

        # Если утром снял отметку или изменил на '-', спрашиваем причину
        today_date = get_today_date_str(now)
        if target_date == today_date and 6 <= now.hour < 20:
            action_desc = None
            if new_status == '-':
                if prev_status == '+':
                    action_desc = "изменили отметку на «Не буду»"
                else:
                    action_desc = "поставили отметку «Не буду»"
            elif new_status is None and prev_status is not None:
                if prev_status == '+':
                    action_desc = "сняли отметку «Буду»"
                elif prev_status == '-':
                    action_desc = "сняли отметку «Не буду»"
                else:
                    action_desc = "сняли отметку"

            if action_desc:
                user_mention = f"@{user.username}" if user.username else f'<a href="tg://user?id={user.id}">{html.escape(user.full_name or "Участник")}</a>'
                text_reason = (
                    f"⚠️ {user_mention}, вы {action_desc} на сегодня ({target_date}).\n"
                    f"Напишите, пожалуйста, причину, почему не сможете прийти?"
                )
                try:
                    await message.answer(text_reason, parse_mode="HTML")
                except Exception as e:
                    logger.warning(f"Не удалось отправить запрос причины: {e}")
