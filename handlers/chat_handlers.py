import logging
import os
import time
from collections import defaultdict

from aiogram import types, Router, Bot
from aiogram.fsm.context import FSMContext
from chains.rag_service import generate_response_with_gpt, process_search_results
from config import IAM_TOKEN, FOLDER_ID, CHROMA_PERSIST_DIR
from chains.chroma_utils import initialize_chroma_client, search_similar_docs

# Инициализация роутера
router = Router()

# Хранение временных меток упоминаний бота в чате
chat_mentions = defaultdict(list)
chat_timeout = {}

@router.message()
async def handle_group_message(message: types.Message, state: FSMContext):
    """
    Обработчик сообщений в групповых чатах. Проверяет упоминание бота и обрабатывает запросы.
    """
    bot_info = await message.bot.get_me()
    bot_username = bot_info.username
    chat_id = message.chat.id
    current_time = time.time()

    # Проверяем, если бот в таймауте из-за частых упоминаний
    if chat_id in chat_timeout and current_time < chat_timeout[chat_id]:
        logging.info(f"Бот временно не отвечает в чате {chat_id} из-за частых упоминаний.")
        return

    # Фильтрация команд
    if message.text and '/' in message.text:
        await message.reply("Использование команд бота в групповых чатах запрещено.")
        return

    # Проверка на упоминание бота в сообщении или подписи
    if message.text and f"@{bot_username}" in message.text:
        await process_mention(message, state, chat_id, current_time)
    elif message.caption and f"@{bot_username}" in message.caption:
        await process_mention(message, state, chat_id, current_time)


async def process_mention(message: types.Message, state: FSMContext, chat_id: int, current_time: float):
    """
    Обрабатывает упоминание бота, проверяет количество упоминаний и ставит таймаут при необходимости.
    """
    # Очищаем устаревшие упоминания (старше 60 секунд)
    chat_mentions[chat_id] = [timestamp for timestamp in chat_mentions[chat_id] if current_time - timestamp < 60]

    # Добавляем новое упоминание
    chat_mentions[chat_id].append(current_time)

    # Проверяем количество упоминаний за последнюю минуту
    if len(chat_mentions[chat_id]) > 3:
        # Устанавливаем таймаут на 5 минут
        chat_timeout[chat_id] = current_time + 300
        logging.info(f"Частые упоминания бота в чате {chat_id}. Бот приостановил ответы на 5 минут.")
        await message.reply("Бот временно не отвечает из-за частых упоминаний. Попробуйте снова через 5 минут.")
    else:
        logging.info(f"Бот упомянут в чате {chat_id} пользователем {message.from_user.id}: {message.text or message.caption}")
        await handle_mention(message, state)


async def handle_mention(message: types.Message, state: FSMContext):
    """
    Обрабатывает текст сообщения, удаляя упоминание бота и запуская поиск в базе знаний.
    """
    bot_info = await message.bot.get_me()
    bot_username = bot_info.username
    text = (message.text or message.caption).replace(f"@{bot_username}", "").strip()

    if not text:
        await message.reply("Вы не задали вопрос. Пожалуйста, введите ваш вопрос.")
        return

    try:
        # Инициализация базы знаний Chroma
        knowledge_base = initialize_chroma_client(
            collection_name="knowledge_base",
            persist_directory=CHROMA_PERSIST_DIR  # Указываем путь к базе данных Chroma
        )

        # Поиск похожих документов
        similar_docs = await search_similar_docs(
            knowledge_base,
            query_text=text,
            k=3,
            user_id=message.from_user.id,
            subject="Вопрос из чата",
            from_user=message.from_user
        )

        if not similar_docs:
            await message.reply("Не найдено релевантных документов. Ваш вопрос зарегистрирован как новый тикет.")
        else:
            # Логирование найденных документов
            logging.info(f"Найдено {len(similar_docs)} похожих документов: {similar_docs}")

            # Создание контекста из найденных документов
            input_documents = [{"page_content": doc.get('text', '')} for doc in similar_docs if isinstance(doc, dict) and 'text' in doc]
            logging.info(f"Формирование запроса к цепочке с input_documents: {input_documents}")

            # Генерация ответа через GPT
            answer = generate_response_with_gpt(IAM_TOKEN, FOLDER_ID, text, input_documents)
            await message.reply(answer)

    except Exception as e:
        logging.error(f"Ошибка при взаимодействии с RAG сервисом: {e}")
        await message.reply(f"Произошла ошибка: {str(e)}")


def extract_subject(text: str) -> str:
    """
    Извлекает тему из сообщения по первому слову или предложению.
    """
    return text.split('.')[0] if '.' in text else text.split()[0]


async def notify_admins_about_question(bot: Bot, message: types.Message, subject: str):
    """
    Уведомляет администраторов о новом вопросе, заданном пользователем.
    """
    user_display_name = message.from_user.username or message.from_user.full_name or "Пользователь без имени"

    # Формируем уведомление
    notification_message = f"Пользователь {user_display_name} задал вопрос с темой '{subject}'."

    # Получаем список администраторов из переменной окружения
    admin_ids = [int(admin_id) for admin_id in os.getenv('ADMIN_IDS').split(',')]

    # Отправляем уведомление каждому админу
    for admin_id in admin_ids:
        try:
            await bot.send_message(admin_id, notification_message)
        except Exception as e:
            logging.error(f"Ошибка при отправке уведомления админу {admin_id}: {e}")