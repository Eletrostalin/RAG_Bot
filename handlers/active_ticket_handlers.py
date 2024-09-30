import logging
from datetime import datetime, timedelta
from aiogram import types, Router
from aiogram.fsm.context import FSMContext
from sqlalchemy import select

from states import AdminStates
from db import get_active_tickets, get_ticket_history, close_ticket_by_admin, async_session, add_answer
from models import Answer, Question, User, MediaFile
from aiogram.filters import Command, StateFilter
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from utils.s3_utils import validate_and_compress_media, send_file_from_url

router = Router()


@router.message(Command(commands=['getticket']), StateFilter(AdminStates.AUTHENTICATED_ADMIN))
async def get_tickets_handler(message: types.Message, state: FSMContext):
    """
    Обработчик команды /getticket для получения списка активных тикетов.
    """
    await show_tickets_page(message, state, page=0)


async def show_tickets_page(message: types.Message, state: FSMContext, page: int):
    """
    Отображает страницу с активными тикетами, поддерживает пагинацию.

    :param message: Сообщение, содержащее команду.
    :param state: Контекст машины состояний.
    :param page: Номер страницы.
    """
    try:
        tickets_per_page = 10
        offset = page * tickets_per_page
        tickets = await get_active_tickets(offset=offset, limit=tickets_per_page)

        if not tickets:
            await message.answer("🔴 Нет активных тикетов.")
            await state.set_state(AdminStates.AUTHENTICATED_ADMIN)
            return

        now = datetime.utcnow()
        keyboard = InlineKeyboardMarkup(inline_keyboard=[])

        for ticket in tickets:
            ticket_age = now - ticket.last_updated
            emoji = "🔥🔥🔥" if ticket_age > timedelta(minutes=2) else ""
            async with async_session() as session:
                result = await session.execute(
                    select(Question).where(Question.ticket_id == ticket.ticket_id).order_by(Question.creation_time)
                )
                question = result.scalars().first()
                subject = question.subject if question else "Без темы"
                button_text = (
                    f"Тикет {ticket.ticket_id}: {subject} "
                    f"(ответил: {ticket.last_admin_name}) {emoji}"
                )
                keyboard.inline_keyboard.append([
                    InlineKeyboardButton(text=button_text, callback_data=f"view_active_ticket_{ticket.ticket_id}")
                ])

        if page > 0:
            keyboard.inline_keyboard.append([
                InlineKeyboardButton(text="⬅️ Предыдущая", callback_data=f"tickets_page_{page - 1}")
            ])
        if len(tickets) == tickets_per_page:
            keyboard.inline_keyboard.append([
                InlineKeyboardButton(text="➡️ Следующая", callback_data=f"tickets_page_{page + 1}")
            ])

        keyboard.inline_keyboard.append(
            [InlineKeyboardButton(text="🏠 Вернуться", callback_data="return_to_authorized")])

        await message.answer("📂 Активные тикеты:", reply_markup=keyboard)
        logging.info(f"Администратор {message.from_user.id} запросил активные тикеты. Страница: {page}")
        await state.update_data(viewing_closed_tickets=False, current_page=page)
        await state.set_state(AdminStates.AUTHENTICATED_ADMIN)
    except Exception as e:
        logging.error(f"Ошибка при запросе активных тикетов администратором {message.from_user.id}: {e}")
        await message.answer("❌ Произошла ошибка при обработке вашего запроса. Попробуйте позже.")


@router.callback_query(lambda c: c.data.startswith('view_active_ticket_'), StateFilter(AdminStates.AUTHENTICATED_ADMIN))
async def view_active_ticket(callback_query: CallbackQuery, state: FSMContext):
    """
    Обработчик для просмотра активного тикета. Показывает историю тикета и
    отображает кнопки для ответа и закрытия тикета.

    :param callback_query: Callback-запрос от нажатия на кнопку.
    :param state: Контекст машины состояний.
    """
    logging.info(f"Просмотр активного тикета. Callback data: {callback_query.data}")
    try:
        ticket_id = int(callback_query.data.split('_')[3])
        history = await get_ticket_history(ticket_id)

        if not history:
            await callback_query.message.edit_text("📝 Нет сообщений в этом тикете.")
            logging.info(f"Тикет {ticket_id} не содержит сообщений.")
            return

        text = f"📋 **Тикет №{ticket_id}**\n\n"
        async with async_session() as session:
            for entry in history:
                result = await session.execute(select(User).where(User.telegram_id == entry.telegram_id))
                user = result.scalars().first()

                user_display_name = user.full_name or user.username or "Неизвестно"

                entry_text = (
                    f"👤 **Имя:** {user_display_name}\n"
                    f"📅 **Дата:** {entry.creation_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                    f"📝 **{'Вопрос' if isinstance(entry, Question) else 'Ответ'}:**\n{entry.text}\n\n"
                )

                text += entry_text

        # Проверка наличия медиафайлов в вопросах и ответах
        has_media_files = False
        async with async_session() as session:
            result = await session.execute(
                select(MediaFile).where(
                    (MediaFile.question_id.in_(select(Question.question_id).where(Question.ticket_id == ticket_id))) |
                    (MediaFile.answer_id.in_(select(Answer.answer_id).where(Answer.ticket_id == ticket_id)))
                )
            )
            media_files = result.scalars().all()
            if media_files:
                has_media_files = True

        # Создаем клавиатуру
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Ответить", callback_data=f"answer_ticket_{ticket_id}")],
            [InlineKeyboardButton(text="🔒 Закрыть Тикет", callback_data=f"close_ticket_{ticket_id}")],
            [InlineKeyboardButton(text="🔙 Вернуться", callback_data="get_active_tickets")]
        ])

        # Добавляем кнопку для скачивания медиа, если файлы есть
        if has_media_files:
            keyboard.inline_keyboard.insert(2, [
                InlineKeyboardButton(text="📥 Скачать медиа", callback_data=f"download_media_{ticket_id}")
            ])

        await callback_query.message.answer(text, parse_mode="HTML", reply_markup=keyboard)
        logging.info(f"Показан тикет {ticket_id} администратору {callback_query.from_user.id}.")
        await state.update_data(ticket_id=ticket_id, ticket_text=text)
        await state.set_state(AdminStates.VIEW_TICKET)
    except Exception as e:
        logging.error(f"Ошибка при просмотре тикета {ticket_id} администратором {callback_query.from_user.id}: {e}")
        await callback_query.message.edit_text("❌ Произошла ошибка при обработке вашего запроса. Попробуйте позже.")


@router.callback_query(lambda c: c.data.startswith('answer_ticket_'), StateFilter(AdminStates.VIEW_TICKET))
async def answer_ticket(callback_query: CallbackQuery, state: FSMContext):
    """
    Обработчик для ответа на тикет.

    :param callback_query: Callback-запрос от нажатия на кнопку.
    :param state: Контекст машины состояний.
    """
    try:
        data = await state.get_data()
        ticket_text = data['ticket_text']
        await callback_query.message.edit_text(f"{ticket_text}\n\n✏️ Пожалуйста, введите ваш ответ.")
        await state.set_state(AdminStates.WAITING_FOR_RESPONSE)
    except Exception as e:
        logging.error(
            f"Ошибка при подготовке к ответу на тикет {callback_query.data.split('_')[3]} администратором {callback_query.from_user.id}: {e}")
        await callback_query.message.edit_text("❌ Произошла ошибка при обработке вашего запроса. Попробуйте позже.")


@router.message(StateFilter(AdminStates.WAITING_FOR_RESPONSE))
async def receive_answer(message: types.Message, state: FSMContext):
    """
    Обработчик для получения ответа администратора.
    Обрабатывает текст ответа и медиафайлы, если они есть, и добавляет их в базу данных.

    :param message: Сообщение, содержащее текст ответа и, возможно, медиафайлы.
    :param state: Контекст машины состояний.
    """
    logging.info(f"Получен ответ от администратора {message.from_user.id} с типом контента {message.content_type}")
    await message.reply("Я могу забрать только одно фото, если у вас их больше дошлите их в личке к тикету")
    try:
        data = await state.get_data()
        ticket_id = data['ticket_id']
        admin_id = message.from_user.id

        # Извлекаем текст ответа из message.text или message.caption
        answer_text = message.text or message.caption  # Используем caption для сообщений с медиа

        if not answer_text:
            await message.answer("❌ Пожалуйста, введите текст ответа.")
            return

        # Проверка наличия медиафайлов (только фото)
        media_files = []
        if message.photo:
            media_files_raw = []
            # Берем самое большое изображение
            largest_photo = message.photo[2]
            logging.info(f"Обрабатываем фото с ID {largest_photo.file_id}")
            file_info = await message.bot.get_file(largest_photo.file_id)
            logging.info(f"Загружаем файл по пути {file_info.file_path}")
            downloaded_file = await message.bot.download_file(file_info.file_path)
            media_files_raw.append({
                'file': downloaded_file,
                'filename': largest_photo.file_id,
                'is_image': True
            })

            # Валидация и сжатие медиафайлов
            media_files = await validate_and_compress_media(media_files_raw, message)
            if not media_files:
                logging.error(f"Ошибка валидации или сжатия медиафайлов.")
                await message.answer("❌ Ошибка при обработке медиафайлов.")
                return

        # Добавляем ответ в базу данных, включая медиафайлы
        new_answer, ticket = await add_answer(admin_id, ticket_id, answer_text, media_files)

        # Проверка успешности добавления ответа и медиа
        logging.info(f"Ответ успешно добавлен, ID ответа: {new_answer.answer_id}")

        # Создаём инлайн-клавиатуру
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📋 Вернуться к тикету",
                                      callback_data=f"view_active_ticket_{ticket.ticket_id}")],
                [InlineKeyboardButton(text="📂 Вернуться к списку тикетов", callback_data="get_tickets")]
            ]
        )

        await message.answer("✅ Ваш ответ был успешно отправлен.", reply_markup=keyboard)

        # Устанавливаем состояние ожидания выбора действия
        await state.set_state(AdminStates.AUTHENTICATED_ADMIN)

    except Exception as e:
        logging.error(f"Ошибка при сохранении ответа: {e}")
        await message.answer("❌ Произошла ошибка. Попробуйте позже.")
        await state.set_state(AdminStates.AUTHENTICATED_ADMIN)


@router.callback_query(lambda c: c.data == 'get_tickets', StateFilter(AdminStates.AUTHENTICATED_ADMIN))
async def return_to_tickets_after_response(callback_query: CallbackQuery, state: FSMContext):
    """
    Обработчик для возврата к списку тикетов после ответа на тикет.

    :param callback_query: Callback-запрос от нажатия на кнопку.
    :param state: Контекст машины состояний.
    """
    try:
        data = await state.get_data()
        page = data.get('current_page', 0)
        await show_tickets_page(callback_query.message, state, page)
    except Exception as e:
        logging.error(f"Ошибка при возврате к списку тикетов администратором {callback_query.from_user.id}: {e}")
        await callback_query.message.edit_text("❌ Произошла ошибка при обработке вашего запроса. Попробуйте позже.")


@router.callback_query(lambda c: c.data == 'get_active_tickets', StateFilter(AdminStates.VIEW_TICKET))
async def return_to_active_tickets(callback_query: CallbackQuery, state: FSMContext):
    """
    Обработчик для возврата к активным тикетам после просмотра тикета.

    :param callback_query: Callback-запрос от нажатия на кнопку.
    :param state: Контекст машины состояний.
    """
    logging.info(f"Возвращение к активным тикетам. Callback data: {callback_query.data}")
    try:
        data = await state.get_data()
        page = data.get('current_page', 0)
        await show_tickets_page(callback_query.message, state, page)
        logging.info(f"Возвращен список активных тикетов на странице {page}.")
    except Exception as e:
        logging.error(
            f"Ошибка при возврате к списку активных тикетов администратором {callback_query.from_user.id}: {e}")
        await callback_query.message.edit_text("❌ Произошла ошибка при обработке вашего запроса. Попробуйте позже.")


@router.callback_query(lambda c: c.data.startswith('close_ticket_'), StateFilter(AdminStates.VIEW_TICKET))
async def close_ticket_handler(callback_query: CallbackQuery, state: FSMContext):
    """
    Обработчик для закрытия тикета.

    :param callback_query: Callback-запрос от нажатия на кнопку.
    :param state: Контекст машины состояний.
    """
    try:
        ticket_id = int(callback_query.data.split('_')[2])
        await close_ticket_by_admin(ticket_id)

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Вернуться к списку тикетов", callback_data="get_active_tickets")]
            ]
        )

        await callback_query.message.edit_text("🔒 Тикет был закрыт.", reply_markup=keyboard)
        await state.set_state(AdminStates.VIEW_TICKET)
    except Exception as e:
        logging.error(f"Ошибка при закрытии тикета {ticket_id} администратором {callback_query.from_user.id}: {e}")
        await callback_query.message.edit_text("❌ Произошла ошибка при обработке вашего запроса. Попробуйте позже.")

@router.callback_query(lambda c: c.data.startswith('download_media_'), StateFilter(AdminStates.VIEW_TICKET))
async def download_media_handler(callback_query: types.CallbackQuery, state: FSMContext):
    try:
        # Получаем ID тикета из callback data
        ticket_id = int(callback_query.data.split('_')[2])

        # Достаем медиафайлы для этого тикета из базы данных
        async with async_session() as session:
            result = await session.execute(
                select(MediaFile).where(MediaFile.ticket_id == ticket_id)
            )
            media_files = result.scalars().all()

        # Проверяем, есть ли медиафайлы для данного тикета
        if not media_files:
            await callback_query.message.answer("❌ Медиафайлы не найдены для этого тикета.")
            return

        # Для каждого медиафайла вызываем send_file_from_url и отправляем файл в чат
        for media in media_files:
            await send_file_from_url(callback_query.bot, callback_query.from_user.id, media.file_url)

        await callback_query.message.answer("✅ Медиафайлы успешно отправлены.")
        logging.info(f"Администратор {callback_query.from_user.id} скачал медиафайлы для тикета {ticket_id}.")

    except Exception as e:
        logging.error(f"Ошибка при загрузке медиафайлов для тикета {ticket_id}: {e}")
        await callback_query.message.answer("❌ Произошла ошибка при загрузке медиафайлов.")


@router.callback_query(lambda c: c.data.startswith('tickets_page_'), StateFilter(AdminStates.AUTHENTICATED_ADMIN))
async def change_tickets_page(callback_query: CallbackQuery, state: FSMContext):
    try:
        page = int(callback_query.data.split('_')[-1])
        await show_tickets_page(callback_query.message, state, page)
    except Exception as e:
        logging.error(f"Ошибка при переходе на страницу тикетов: {e}")
        await callback_query.message.edit_text("❌ Произошла ошибка при обработке вашего запроса. Попробуйте позже.")

@router.callback_query(lambda c: c.data == 'return_to_authorized', StateFilter(AdminStates.AUTHENTICATED_ADMIN))
async def return_to_authorized(callback_query: CallbackQuery, state: FSMContext):
    try:
        await callback_query.message.edit_text("🏠 Вы вернулись в меню администратора. Выберите команду ниже")
        await state.set_state(AdminStates.AUTHENTICATED_ADMIN)
        logging.info(f"Администратор {callback_query.from_user.id} вернулся в меню.")
    except Exception as e:
        logging.error(f"Ошибка при возврате в меню администратором {callback_query.from_user.id}: {e}")
        await callback_query.message.edit_text("❌ Произошла ошибка при обработке вашего запроса. Попробуйте позже.")

