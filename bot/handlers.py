import html
import logging
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.filters.chat_member_updated import ChatMemberUpdatedFilter, MEMBER, ADMINISTRATOR
from aiogram.types import Message, CallbackQuery, ChatMemberUpdated
from datetime import datetime
from bot.keyboards import get_rollcall_keyboard
from bot.utils import (
    get_msk_now, get_target_date_str, get_today_date_str,
    get_current_poll_date_str, format_poll_text
)
from bot.filters import is_admin
from config import ALLOWED_CHAT_IDS
from db import (
    register_chat, update_chat_sheet, get_chat_sheet,
    save_vote, get_votes_for_date, save_poll_message_id,
    get_poll_message_id, get_target_date_by_message_id, set_checked_in,
    get_user_vote, remove_vote, get_all_dates_for_chat
)
from services.sheets import async_sync_rollcall_to_sheet

router = Router()
logger = logging.getLogger(__name__)

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
        "Каждый день в 20:00 (по МСК) я буду спрашивать, кто будет завтра.\n"
        "А утром с 06:00 до 11:00 (по МСК) буду отслеживать, кто пришел!\n\n"
        "🔧 <b>Команды:</b>\n"
        "/start_poll — Запустить перекличку вручную (по умолчанию на завтра, можно <code>/start_poll today</code>)\n"
        "/setup_sheet &lt;URL&gt; — Привязать Google Таблицу\n"
        "/sync_sheet — Синхронизировать всю историю перекличек в Google Таблицу",
        parse_mode="HTML"
    )

@router.message(Command("setup_sheet"))
async def cmd_setup_sheet(message: Message):
    if not message.from_user or not is_admin(message.from_user.id):
        await message.answer("⛔ <b>У вас нет прав для настройки таблицы.</b>", parse_mode="HTML")
        return

    if message.chat.type in ["group", "supergroup"] and message.chat.id not in ALLOWED_CHAT_IDS:
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
    try:
        dates.sort(key=lambda d: datetime.strptime(d, "%d.%m.%Y"))
    except Exception:
        pass

    synced_count = 0
    for d in dates:
        votes = await get_votes_for_date(message.chat.id, d)
        if votes:
            await async_sync_rollcall_to_sheet(sheet_url, d, votes)
            synced_count += 1

    await status_msg.edit_text(
        f"✅ <b>Google Таблица успешно привязана к чату!</b>\n"
        f"Синхронизировано перекличек за прошлые дни: <b>{synced_count}</b>.",
        parse_mode="HTML"
    )

@router.message(Command("sync_sheet"))
async def cmd_sync_sheet(message: Message):
    if not message.from_user or not is_admin(message.from_user.id):
        await message.answer("⛔ <b>У вас нет прав для выполнения этой команды.</b>", parse_mode="HTML")
        return

    if message.chat.type in ["group", "supergroup"] and message.chat.id not in ALLOWED_CHAT_IDS:
        return

    sheet_url = await get_chat_sheet(message.chat.id)
    if not sheet_url:
        await message.answer("⚠️ Google Таблица еще не привязана. Используйте <code>/setup_sheet &lt;URL&gt;</code>", parse_mode="HTML")
        return

    status_msg = await message.answer("⏳ Запущена синхронизация всех перекличек в Google Таблицу...", parse_mode="HTML")

    dates = await get_all_dates_for_chat(message.chat.id)
    try:
        dates.sort(key=lambda d: datetime.strptime(d, "%d.%m.%Y"))
    except Exception:
        pass

    synced_count = 0
    for d in dates:
        votes = await get_votes_for_date(message.chat.id, d)
        if votes:
            await async_sync_rollcall_to_sheet(sheet_url, d, votes)
            synced_count += 1

    await status_msg.edit_text(
        f"✅ <b>Синхронизация завершена!</b>\n"
        f"Выгружено дат в Google Таблицу: <b>{synced_count}</b>.",
        parse_mode="HTML"
    )

@router.message(Command("start_poll"))
async def cmd_start_poll(message: Message):
    if not message.from_user or not is_admin(message.from_user.id):
        await message.answer("⛔ <b>У вас нет прав для запуска переклички.</b>", parse_mode="HTML")
        return

    if message.chat.type in ["group", "supergroup"] and message.chat.id not in ALLOWED_CHAT_IDS:
        try:
            await message.bot.leave_chat(message.chat.id)
        except Exception:
            pass
        return

    if message.chat.type in ["group", "supergroup"]:
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

    # Если нажал ту же кнопку повторно — снимаем голос
    if prev_status == status:
        await remove_vote(chat_id, target_date, user.id)
        new_status = None
        ans_text = "Ваша отметка снята!"
    else:
        await save_vote(
            chat_id=chat_id,
            target_date=target_date,
            user_id=user.id,
            username=user.username or "",
            full_name=user.full_name or "Участник",
            status=status
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
        await async_sync_rollcall_to_sheet(sheet_url, target_date, votes)

    # Если утром снял отметку (было '+', стало '-' или снято вовсе), спрашиваем причину
    now = get_msk_now()
    today_date = get_today_date_str(now)
    if prev_status == '+' and target_date == today_date and 6 <= now.hour < 20:
        if new_status == '-':
            action_desc = "изменили отметку на «Не буду»"
        elif new_status is None:
            action_desc = "сняли отметку «Буду»"
        else:
            action_desc = None

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
            "🔧 <b>Команды:</b>\n"
            "/start_poll — Запустить опрос вручную\n"
            "/setup_sheet &lt;URL&gt; — Привязать Google Таблицу",
            parse_mode="HTML"
        )
        return

    # 1. Отслеживание прихода (с 06:00 до 20:00 по МСК) на СЕГОДНЯ (учитываем текст, фото и любые сообщения)
    if 6 <= now.hour < 20:
        today_date = get_today_date_str(now)
        was_checked_in = await set_checked_in(chat_id, today_date, user.id)
        # Синхронизируем статус прихода с Google Таблицей только если статус изменился
        if was_checked_in:
            sheet_url = await get_chat_sheet(chat_id)
            if sheet_url:
                votes = await get_votes_for_date(chat_id, today_date)
                await async_sync_rollcall_to_sheet(sheet_url, today_date, votes)

    # 2. Быстрый ответ + или - на опрос (текстом или в подписи к фото)
    if text in ["+", "-"]:
        target_date = None
        if message.reply_to_message:
            target_date = await get_target_date_by_message_id(chat_id, message.reply_to_message.message_id)

        if not target_date:
            target_date = get_current_poll_date_str(now)

        prev_status = await get_user_vote(chat_id, target_date, user.id)

        # Если отправил тот же знак повторно — снимаем голос
        if prev_status == text:
            await remove_vote(chat_id, target_date, user.id)
            new_status = None
        else:
            await save_vote(
                chat_id=chat_id,
                target_date=target_date,
                user_id=user.id,
                username=user.username or "",
                full_name=user.full_name or "Участник",
                status=text
            )
            new_status = text

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
            await async_sync_rollcall_to_sheet(sheet_url, target_date, votes)

        # Если утром снял отметку (было '+', стало '-' или снято вовсе), спрашиваем причину
        today_date = get_today_date_str(now)
        if prev_status == '+' and target_date == today_date and 6 <= now.hour < 20:
            if new_status == '-':
                action_desc = "изменили отметку на «Не буду»"
            elif new_status is None:
                action_desc = "сняли отметку «Буду»"
            else:
                action_desc = None

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
