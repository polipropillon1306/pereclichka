import asyncio
import logging
import sys
from aiogram import Bot, Dispatcher
from config import BOT_TOKEN
from db import init_db
from bot.handlers import router
from bot.middlewares import ThrottlingMiddleware
from bot.scheduler import setup_scheduler
from services.sheets_queue import flush_all_sheet_syncs

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)

async def main():
    if not BOT_TOKEN:
        logging.error("BOT_TOKEN не установлен в файле .env!")
        return

    # Инициализация базы данных
    await init_db()

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()

    # Подключение Middleware защиты от спама и атак (Throttling)
    throttling_middleware = ThrottlingMiddleware()
    dp.callback_query.middleware(throttling_middleware)
    dp.message.middleware(throttling_middleware)

    dp.include_router(router)

    # Запуск планировщика
    scheduler = setup_scheduler(bot)
    scheduler.start()

    logging.info("Бот для переклички успешно запущен!")
    try:
        await dp.start_polling(bot)
    finally:
        await flush_all_sheet_syncs()
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
