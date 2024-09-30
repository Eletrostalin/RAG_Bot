import logging
from aiogram import types, Bot, Router, F
from aiogram.fsm.context import FSMContext
from config import ADMIN_IDS
from states import AdminStates, UserStates
from aiogram.filters import Command, StateFilter

# Импорт обработчиков
from handlers.admin_handlers import (
    get_users_handler, admin_home, load_embeddings_handler,
    show_embeddings_handler, clear_chroma_handler,
    upload_txt_handler, list_files_handler
)
from handlers.active_ticket_handlers import get_tickets_handler
from handlers.closed_ticket_handlers import get_closed_tickets_handler
from handlers.user_handlers import show_tickets_handler, show_closed_tickets_handler
from utils.keyboards import get_admin_inline_keyboard, get_knowledge_base_inline_keyboard

# Инициализация роутера
router = Router()

# Команды администратора
admin_commands = {
    "/getusers": get_users_handler,
    "/home": admin_home,
    "/getticket": get_tickets_handler,
    "/getclosedticket": get_closed_tickets_handler,
    "/load_embeddings": load_embeddings_handler,
    "/showembeddings": show_embeddings_handler,
    "/clear_chroma": clear_chroma_handler,
    "/uploadtxt": upload_txt_handler,
    "/listfiles": list_files_handler
}

# Команды пользователя
user_commands = {
    "/showtickets": show_tickets_handler,
    "/showclosedtickets": show_closed_tickets_handler
}


async def set_admin_commands(bot: Bot):
    """
    Устанавливает команды для администраторов.
    """
    admin_commands = [
        types.BotCommand(command="/getusers", description="📋 Получить список пользователей"),
        types.BotCommand(command="/getticket", description="📂 Показать активные тикеты"),
        types.BotCommand(command="/getclosedticket", description="📂 Показать закрытые тикеты"),
        types.BotCommand(command="/home", description="🏠 Вернуться в меню администратора"),
        types.BotCommand(command="/load_embeddings", description="📥 Загрузить эмбеддинги в Chroma"),
        types.BotCommand(command="/showembeddings", description="🔍 Просмотреть эмбеддинги"),
        types.BotCommand(command="/clear_chroma", description="🗑️ Очистить коллекцию Chroma"),
        types.BotCommand(command="/uploadtxt", description="📤 Загрузить txt файл в S3"),
        types.BotCommand(command="/listfiles", description="📄 Список файлов в S3")
    ]
    await bot.set_my_commands(admin_commands)
    logging.info("Admin commands set.")


async def set_user_commands(bot: Bot):
    """
    Устанавливает команды для пользователей.
    """
    user_commands = [
        types.BotCommand(command="/showtickets", description="📂 Показать мои тикеты"),
        types.BotCommand(command="/showclosedtickets", description="📂 Показать закрытые тикеты")
    ]
    await bot.set_my_commands(user_commands)
    logging.info("User commands set.")


@router.message(Command(commands=['start']))
async def start_handler(message: types.Message, state: FSMContext):
    """
    Обрабатывает команду /start. Проверяет, является ли пользователь администратором, и устанавливает соответствующие команды.
    """
    if message.chat.type != 'private':
        logging.info(f"Команда /start вызвана в чате {message.chat.id}. Игнорирование.")
        return

    user_id = message.from_user.id

    if user_id in ADMIN_IDS:
        await state.set_state(AdminStates.AUTHENTICATED_ADMIN)
        await set_admin_commands(message.bot)
        await message.answer(
            f"✅ Вы успешно аутентифицированы как администратор, {message.from_user.first_name}.",
            reply_markup=get_admin_inline_keyboard()  # Отправляем инлайн-кнопки для админа
        )
        logging.info(f"Администратор {user_id} ({message.from_user.first_name}) успешно аутентифицирован.")
    else:
        # Логика для пользователей, не являющихся администраторами
        await message.answer("❌ Доступ запрещен. Вы не зарегистрированы в системе.")
        logging.warning(f"Неизвестный пользователь {user_id} попытался выполнить команду /start.")


@router.message(StateFilter(AdminStates.AUTHENTICATED_ADMIN))
async def handle_private_message(message: types.Message, state: FSMContext):
    """
    Обрабатывает команды администраторов в личных сообщениях.
    """
    if message.chat.type == 'private':
        if message.text.startswith('/'):
            command = message.text.split()[0]
            handler = admin_commands.get(command)
            if handler:
                logging.info(f"Администратор {message.from_user.id} вызвал команду {command}.")
                await handler(message, state)
            else:
                await message.answer(f"Команда не распознана: {command}")
        else:
            await message.answer("Пожалуйста, используйте одну из команд администратора.")
        logging.info(f"Администратор {message.from_user.id} отправил сообщение в личных сообщениях.")
    else:
        logging.info(f"Игнорирование сообщения из чата {message.chat.id}.")


@router.message(StateFilter(UserStates.AUTHENTICATED_USER))
async def handle_user_message(message: types.Message, state: FSMContext):
    """
    Обрабатывает команды пользователей в личных сообщениях.
    """
    if message.chat.type == 'private':
        if message.text.startswith('/'):
            command = message.text.split()[0]
            handler = user_commands.get(command)
            if handler:
                logging.info(f"Пользователь {message.from_user.id} вызвал команду {command}.")
                await handler(message)
            else:
                await message.answer("Команда не распознана.")
        else:
            logging.info(f"Пользователь {message.from_user.id} отправил сообщение в личных сообщениях. Сообщение проигнорировано.")
    else:
        logging.info(f"Игнорирование сообщения из чата {message.chat.id}.")


# Обработчики callback'ов для инлайн-кнопок администратора
@router.callback_query(StateFilter(AdminStates.AUTHENTICATED_ADMIN), F.data == "getticket")
async def get_ticket_callback_handler(callback: types.CallbackQuery, state: FSMContext):
    await get_tickets_handler(callback.message, state)


@router.callback_query(StateFilter(AdminStates.AUTHENTICATED_ADMIN), F.data == "getclosedticket")
async def get_closed_ticket_callback_handler(callback: types.CallbackQuery, state: FSMContext):
    await get_closed_tickets_handler(callback.message, state)


@router.callback_query(StateFilter(AdminStates.AUTHENTICATED_ADMIN), F.data == "getusers")
async def get_users_callback_handler(callback: types.CallbackQuery, state: FSMContext):
    await get_users_handler(callback.message, state)


@router.callback_query(StateFilter(AdminStates.AUTHENTICATED_ADMIN), F.data == "knowledge_base")
async def knowledge_base_callback_handler(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer(
        "📚 Выберите действие для взаимодействия с базой знаний:",
        reply_markup=get_knowledge_base_inline_keyboard()
    )


# Обработчики callback'ов для взаимодействия с базой знаний
@router.callback_query(StateFilter(AdminStates.AUTHENTICATED_ADMIN), F.data == "load_embeddings")
async def load_embeddings_callback_handler(callback: types.CallbackQuery, state: FSMContext):
    await load_embeddings_handler(callback.message, state)


@router.callback_query(StateFilter(AdminStates.AUTHENTICATED_ADMIN), F.data == "showembeddings")
async def show_embeddings_callback_handler(callback: types.CallbackQuery, state: FSMContext):
    await show_embeddings_handler(callback.message, state)


@router.callback_query(StateFilter(AdminStates.AUTHENTICATED_ADMIN), F.data == "clear_chroma")
async def clear_chroma_callback_handler(callback: types.CallbackQuery, state: FSMContext):
    await clear_chroma_handler(callback.message, state)


@router.callback_query(StateFilter(AdminStates.AUTHENTICATED_ADMIN), F.data == "uploadtxt")
async def upload_txt_callback_handler(callback: types.CallbackQuery, state: FSMContext):
    await upload_txt_handler(callback.message, state)


@router.callback_query(StateFilter(AdminStates.AUTHENTICATED_ADMIN), F.data == "listfiles")
async def list_files_callback_handler(callback: types.CallbackQuery, state: FSMContext):
    await list_files_handler(callback.message, state)