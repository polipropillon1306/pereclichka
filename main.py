import asyncio
import logging
import sys
from aiogram import Bot, Dispatcher
from config import BOT_TOKEN
from db import init_db
from bot.handlers import router
from bot.scheduler import setup_scheduler

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
    dp.include_router(router)

    # Запуск планировщика
    scheduler = setup_scheduler(bot)
    scheduler.start()

    logging.info("Бот для переклички успешно запущен!")
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
