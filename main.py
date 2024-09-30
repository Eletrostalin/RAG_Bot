import logging
import asyncio
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.enums import ParseMode
from aiogram.client.bot import DefaultBotProperties
from db import init_db, apply_migrations
from handlers.auth_handlers import router as auth_router
from handlers.chat_handlers import router as chat_router
from handlers.admin_handlers import router as admin_router
from handlers.user_handlers import router as user_router
from handlers.active_ticket_handlers import router as active_ticket_router
from handlers.closed_ticket_handlers import router as closed_ticket_router
from config import TOKEN
from fastapi import FastAPI
from chains.rag_service import app as rag_app  # Импорт FastAPI приложения для RAG
import uvicorn
from pydantic_settings import BaseSettings  # Импорт BaseSettings для конфигурации
from utils.iam_token_updater import update_iam_token


class GlobalConfig(BaseSettings):
    """Глобальная конфигурация приложения на основе Pydantic BaseSettings."""

    class Config:
        arbitrary_types_allowed = True  # Разрешает использование нестандартных типов данных


# Инициализация глобальной конфигурации
config = GlobalConfig()

# Настройка логирования в консоль
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()]  # Использование StreamHandler для вывода логов
)
logger = logging.getLogger(__name__)

# Инициализация бота и диспетчера с хранением состояний в памяти
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Включение маршрутов для различных функциональных частей приложения
dp.include_router(auth_router)
dp.include_router(admin_router)
dp.include_router(user_router)
dp.include_router(active_ticket_router)
dp.include_router(closed_ticket_router)
dp.include_router(chat_router)

# Инициализация FastAPI приложения
api_app = FastAPI()

# Монтирование FastAPI приложения для RAG под /rag
api_app.mount("/rag", rag_app)


async def on_startup(dispatcher: Dispatcher):
    """
    Функция, которая выполняется при старте приложения.
    Инициализирует базу данных, обновляет IAM токен и логирует информацию о боте.
    """
    logger.info("Функция on_startup запущена...")

    # Инициализация базы данных
    await init_db()

    # Обновление и сохранение IAM токена при запуске
    logger.info("Попытка обновления IAM токена при запуске...")
    iam_token = update_iam_token()  # Получение нового IAM токена
    if iam_token:
        dispatcher['iam_token'] = iam_token  # Сохранение токена в диспетчере
        logger.info("IAM токен успешно обновлен и сохранен.")
    else:
        logger.error("Ошибка при обновлении IAM токена.")

    # Получение информации о боте
    bot_info = await bot.get_me()
    dispatcher['bot_username'] = bot_info.username  # Сохранение имени пользователя бота
    logger.info(f"Имя пользователя бота: {dispatcher['bot_username']}")
    logger.info("Бот успешно запущен.")


async def start_fastapi_server():
    """
    Запуск сервера FastAPI на порту 8000.
    """
    config = uvicorn.Config(api_app, host="0.0.0.0", port=8000, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()


async def main():
    """
    Основная функция запуска приложения. Параллельно запускает поллинг для бота и сервер FastAPI.
    """
    logger.info("Запуск бота...")

    # Применение миграций к базе данных перед запуском бота
    await apply_migrations()

    # Инициализация диспетчера перед поллингом
    await on_startup(dp)

    # Параллельный запуск бота и сервера FastAPI
    await asyncio.gather(
        dp.start_polling(bot),  # Запуск поллинга для бота
        start_fastapi_server()  # Запуск FastAPI сервера
    )


if __name__ == '__main__':
    # Запуск основного асинхронного события
    try:
        asyncio.run(main())
    except Exception as e:
        logger.critical(f"Критическая ошибка при запуске бота: {e}")