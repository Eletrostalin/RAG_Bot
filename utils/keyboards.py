from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from handlers.closed_ticket_handlers import *
from handlers.auth_handlers import *

router = Router()

def get_admin_inline_keyboard():
    # Создаем билдер для инлайн-клавиатуры
    builder = InlineKeyboardBuilder()

    # Группируем кнопки в несколько строк
    builder.row(
        InlineKeyboardButton(text="📂 Тикеты", callback_data="getticket"),
        InlineKeyboardButton(text="📂 Закрытые тикеты", callback_data="getclosedticket")
    )
    builder.row(
        InlineKeyboardButton(text="📋 Список пользователей", callback_data="getusers"),
        InlineKeyboardButton(text="📚 База знаний", callback_data="knowledge_base")
    )

    # Возвращаем готовую клавиатуру
    return builder.as_markup()


def get_knowledge_base_inline_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    # Добавляем кнопки для взаимодействия с базой знаний
    builder.row(
        InlineKeyboardButton(text="📥 Загрузить эмбеддинги", callback_data="load_embeddings"),
        InlineKeyboardButton(text="🔍 Просмотреть эмбеддинги", callback_data="showembeddings"),
        InlineKeyboardButton(text="🗑️ Очистить коллекцию", callback_data="clear_chroma")
    )
    builder.row(
        InlineKeyboardButton(text="📤 Загрузить txt файл", callback_data="uploadtxt"),
        InlineKeyboardButton(text="📄 Список файлов", callback_data="listfiles")
    )

    # Возвращаем клавиатуру
    return builder.as_markup()


def get_user_keyboard():
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="/showtickets 📂 Показать мои тикеты")],
            [KeyboardButton(text="/showclosedtickets 📂 Показать закрытые тикеты")]
        ],
        resize_keyboard=True
    )
    return keyboard

