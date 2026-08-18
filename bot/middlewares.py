import time
import logging
from typing import Callable, Dict, Any, Awaitable
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, CallbackQuery, Message
from config import RATE_LIMIT_CALLBACK, RATE_LIMIT_MESSAGE, ADMIN_IDS

logger = logging.getLogger(__name__)

class ThrottlingMiddleware(BaseMiddleware):
    """
    Middleware для предотвращения флуда, спама кликами и DoS-атак на бота.
    Ограничивает частоту нажатия inline-кнопок и отправки сообщений.
    """
    def __init__(self):
        super().__init__()
        # user_id -> last_timestamp
        self._last_callback_time: Dict[int, float] = {}
        self._last_message_time: Dict[int, float] = {}
        # user_id -> (strike_count, strikes_window_start)
        self._callback_strikes: Dict[int, tuple] = {}
        # user_id -> block_until_timestamp
        self._blocked_users: Dict[int, float] = {}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        now = time.monotonic()

        # 1. Обработка CallbackQuery (нажатия на inline-кнопки)
        if isinstance(event, CallbackQuery):
            user = event.from_user
            if not user:
                return await handler(event, data)

            user_id = user.id

            # Проверяем, не находится ли пользователь во временной блокировке
            blocked_until = self._blocked_users.get(user_id, 0)
            if now < blocked_until:
                remaining = int(blocked_until - now) + 1
                try:
                    await event.answer(
                        f"⛔ Слишком много нажатий!\nБлокировка еще {remaining} сек.",
                        show_alert=True
                    )
                except Exception:
                    pass
                return  # Прерываем обработку

            # Отслеживаем частоту нажатий
            last_time = self._last_callback_time.get(user_id, 0)
            time_diff = now - last_time

            # Анализ спам-серий (нажатий в секунду)
            strike_data = self._callback_strikes.get(user_id, (0, now))
            count, window_start = strike_data
            if now - window_start > 5.0:
                count = 1
                window_start = now
            else:
                count += 1
            self._callback_strikes[user_id] = (count, window_start)

            # Если пользователь сделал больше 6 кликов за 5 секунд — временный мут на 30 сек
            if count >= 7:
                self._blocked_users[user_id] = now + 30.0
                logger.warning(f"Пользователь {user_id} ({user.username}) заблокирован на 30с за клик-флуд ({count} кликов за 5с)")
                try:
                    await event.answer(
                        "⚠️ Обнаружен автокликер / частый спам!\nВы временно заблокированы на 30 секунд.",
                        show_alert=True
                    )
                except Exception:
                    pass
                return

            # Обычный Rate Limit между нажатиями (например, 2 секунды)
            if time_diff < RATE_LIMIT_CALLBACK:
                try:
                    await event.answer("⏳ Не нажимайте так часто!", show_alert=False)
                except Exception:
                    pass
                return  # Прерываем обработку

            self._last_callback_time[user_id] = now
            return await handler(event, data)

        # 2. Обработка текстовых Message
        if isinstance(event, Message):
            user = event.from_user
            if not user or user.id in ADMIN_IDS:
                return await handler(event, data)

            user_id = user.id
            last_msg_time = self._last_message_time.get(user_id, 0)
            time_diff = now - last_msg_time

            # Ограничение частоты команд для обычных пользователей
            if event.text and event.text.startswith("/") and time_diff < RATE_LIMIT_MESSAGE:
                return  # Игнорируем спам командами

            self._last_message_time[user_id] = now
            return await handler(event, data)

        return await handler(event, data)
