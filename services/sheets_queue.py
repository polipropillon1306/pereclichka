import asyncio
import logging
from typing import Dict, Tuple
from config import SHEETS_SYNC_DEBOUNCE_SECONDS
from db import get_votes_for_date, get_all_known_users_for_chat
from services.sheets import async_sync_rollcall_to_sheet

logger = logging.getLogger(__name__)

# Хранилище отложенных задач: (chat_id, target_date) -> asyncio.Task
_pending_tasks: Dict[Tuple[int, str], asyncio.Task] = {}
_tasks_lock = asyncio.Lock()

async def _delayed_sync(sheet_url: str, target_date: str, chat_id: int, bot, delay: float):
    try:
        if delay > 0:
            await asyncio.sleep(delay)

        # Извлекаем самые актуальные данные из базы на момент синхронизации
        votes = await get_votes_for_date(chat_id, target_date)
        known_users = await get_all_known_users_for_chat(chat_id)

        await async_sync_rollcall_to_sheet(
            sheet_url=sheet_url,
            target_date=target_date,
            votes=votes,
            bot=bot,
            chat_id=chat_id,
            known_users=known_users
        )
    except asyncio.CancelledError:
        # Задача была перезапущена новым кликом (debounce)
        pass
    except Exception as e:
        logger.error(f"Ошибка в фоновой синхронизации Google Sheets для чата {chat_id} / {target_date}: {e}")
    finally:
        async with _tasks_lock:
            key = (chat_id, target_date)
            if _pending_tasks.get(key) is asyncio.current_task():
                _pending_tasks.pop(key, None)

async def queue_sheet_sync(sheet_url: str, target_date: str, chat_id: int, bot, delay: float = None):
    """
    Ставит задачу синхронизации с Google Sheets в очередь с задержкой (debounce).
    Если за время задержки поступают новые голоса, таймер сбрасывается и выполняется
    только 1 пакетный запрос с актуальным состоянием.
    """
    if not sheet_url:
        return

    if delay is None:
        delay = SHEETS_SYNC_DEBOUNCE_SECONDS

    key = (chat_id, target_date)
    async with _tasks_lock:
        existing_task = _pending_tasks.get(key)
        if existing_task and not existing_task.done():
            existing_task.cancel()

        task = asyncio.create_task(
            _delayed_sync(sheet_url, target_date, chat_id, bot, delay)
        )
        _pending_tasks[key] = task

async def flush_all_sheet_syncs():
    """Немедленно завершает все ожидающие задачи синхронизации (например, при остановке бота)"""
    async with _tasks_lock:
        tasks = list(_pending_tasks.values())
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        _pending_tasks.clear()
