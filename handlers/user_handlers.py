from config import ADMIN_IDS
from aiogram import Router
from aiogram.fsm.context import FSMContext
from states import UserStates
from db import *
from models import User, Question, Answer
from aiogram.filters import Command, StateFilter
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

from utils.s3_utils import validate_and_compress_media, send_file_from_url

router = Router()

@router.message(Command(commands=['showtickets']), StateFilter(UserStates.AUTHENTICATED_USER))
async def show_tickets_handler(message: types.Message):
    user_id = message.from_user.id
    await show_user_tickets(message, user_id)

async def show_user_tickets(message: types.Message, user_id: int):
    logging.info(f"Запрашиваем тикеты для пользователя: {user_id}")  # Логируем ID пользователя

    tickets = await get_user_tickets(user_id)

    if not tickets:
        await message.answer("🔴 У вас нет активных тикетов.")
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[])

    for ticket in tickets:
        async with async_session() as session:
            # Получаем последний ответ
            result = await session.execute(
                select(Answer).where(Answer.ticket_id == ticket.ticket_id).order_by(Answer.answer_time.desc())
            )
            last_answer = result.scalars().first()
            # Если последний ответ от админа — добавляем замочек
            emoji = "🔒" if not ticket.active and ticket.closed_by_user else (
                "🔥" if last_answer and last_answer.telegram_id in ADMIN_IDS else "")

            # Получаем тему вопроса
            result = await session.execute(
                select(Question).where(Question.ticket_id == ticket.ticket_id).order_by(Question.creation_time)
            )
            question = result.scalars().first()
            subject = question.subject if question else "Без темы"

            # Формируем текст кнопки
            button_text = f"Тикет {ticket.ticket_id}: {subject} {emoji}"
            keyboard.inline_keyboard.append(
                [InlineKeyboardButton(text=button_text, callback_data=f"view_user_ticket_{ticket.ticket_id}")])

    await message.answer("📂 Ваши тикеты:", reply_markup=keyboard)
    logging.info(f"Пользователь {message.from_user.id} запросил свои тикеты.")

@router.callback_query(lambda c: c.data.startswith('view_user_ticket_'), StateFilter(UserStates.AUTHENTICATED_USER))
async def view_user_ticket(callback_query: CallbackQuery, state: FSMContext):
    logging.info(f"Просмотр тикета пользователем. Callback data: {callback_query.data}")
    try:
        ticket_id = int(callback_query.data.split('_')[3])
        history = await get_ticket_history(ticket_id)

        if not history:
            await callback_query.message.edit_text("📝 Нет сообщений в этом тикете.")
            logging.info(f"Тикет {ticket_id} не содержит сообщений.")
            return

        text = f"📋 **Ваш тикет №{ticket_id}**\n\n"
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

        # Проверка наличия медиафайлов
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
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="✏️ Ответить", callback_data=f"user_answer_ticket_{ticket_id}")],
                [InlineKeyboardButton(text="🔒 Закрыть тикет", callback_data=f"close_user_ticket_{ticket_id}")],  # Кнопка для закрытия тикета
                [InlineKeyboardButton(text="🔙 Вернуться", callback_data="return_to_user_tickets")]
            ]
        )

        # Добавляем кнопку для скачивания медиа, если файлы есть
        if has_media_files:
            keyboard.inline_keyboard.insert(2, [InlineKeyboardButton(text="📥 Скачать медиа", callback_data=f"download_media_{ticket_id}")])

        await callback_query.message.answer(text, parse_mode="HTML", reply_markup=keyboard)
        logging.info(f"Пользователю показан тикет {ticket_id}.")
        await state.update_data(ticket_id=ticket_id, ticket_text=text)
        await state.set_state(UserStates.VIEW_TICKET)

    except Exception as e:
        logging.error(f"Ошибка при просмотре тикета пользователем {callback_query.from_user.id}: {e}")
        await callback_query.message.edit_text("❌ Произошла ошибка при обработке вашего запроса. Попробуйте позже.")

@router.callback_query(lambda c: c.data.startswith('user_answer_ticket_'), StateFilter(UserStates.VIEW_TICKET))
async def user_reply_ticket(callback_query: CallbackQuery, state: FSMContext):
    try:
        await callback_query.message.edit_text("✏️ Пожалуйста, введите ваш ответ.")
        await state.set_state(UserStates.WAITING_FOR_RESPONSE)
    except Exception as e:
        logging.error(f"Ошибка при подготовке к ответу на тикет {callback_query.data.split('_')[3]} пользователем {callback_query.from_user.id}: {e}")
        await callback_query.message.edit_text("❌ Произошла ошибка при обработке вашего запроса. Попробуйте позже.")

@router.callback_query(lambda c: c.data.startswith('download_media_'), StateFilter(UserStates.VIEW_TICKET))
async def download_media_handler(callback_query: types.CallbackQuery, state: FSMContext):
    try:
        # Получаем ID тикета из callback data
        ticket_id = int(callback_query.data.split('_')[2])

        # Достаем медиафайлы для этого тикета из базы данных
        async with async_session() as session:
            result = await session.execute(
                select(MediaFile).where(MediaFile.question_id.in_(
                    select(Question.question_id).where(Question.ticket_id == ticket_id)
                ))
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
        await state.set_state(UserStates.AUTHENTICATED_USER)
        logging.info(f"Пользователь {callback_query.from_user.id} скачал медиафайлы для тикета {ticket_id}.")

    except Exception as e:
        logging.error(f"Ошибка при загрузке медиафайлов для тикета {ticket_id}: {e}")
        await callback_query.message.answer("❌ Произошла ошибка при загрузке медиафайлов.")


@router.message(StateFilter(UserStates.WAITING_FOR_RESPONSE))
async def user_receive_answer(message: types.Message, state: FSMContext):
    logging.info(f"Получен ответ от пользователя {message.from_user.id} с типом контента {message.content_type}")

    try:
        data = await state.get_data()
        ticket_id = data['ticket_id']
        user_id = message.from_user.id

        # Обработка медиафайлов (несколько фото)
        media_files = []

        # Извлечение текста ответа из message.text или message.caption
        answer_text = message.text or message.caption  # Используем caption для сообщений с медиа
        print(answer_text)
        if not answer_text:
            await message.answer("❌ Пожалуйста, введите текст ответа.")
            return

        if message.photo:
            # Проходим по каждому фото в сообщении
            for photo in message.photo:
                # Получаем информацию о файле и загружаем его
                file_info = await message.bot.get_file(photo.file_id)
                downloaded_file = await message.bot.download_file(file_info.file_path)
                media_files_raw = [{
                    'file': downloaded_file,
                    'filename': file_info.file_path.split('/')[-1],
                    'is_image': True
                }]

                # Валидация и сжатие медиафайлов
                validated_files = await validate_and_compress_media(media_files_raw, message)
                if not validated_files:
                    await message.answer("❌ Ошибка при обработке медиафайла.")
                    continue  # Переходим к следующему фото, если возникла ошибка

                # Добавляем валидированные файлы в общий список
                media_files.extend(validated_files)

        # Извлечение темы предыдущего вопроса
        async with async_session() as session:
            result = await session.execute(
                select(Question.subject).where(Question.ticket_id == ticket_id).order_by(Question.creation_time.desc())
            )
            subject = result.scalars().first()

            result = await session.execute(select(Ticket).where(Ticket.ticket_id == ticket_id))
            ticket = result.scalars().first()
            if ticket:
                ticket.active = True
                await session.commit()

        # Добавление нового вопроса
        new_question = await add_question_to_ticket(
            user_id=user_id,
            ticket_id=ticket_id,
            question_text=answer_text,
            subject=subject,
            media_files=media_files  # Передаем массив медиафайлов
        )

        # Уведомление администратора
        async with async_session() as session:
            result = await session.execute(
                select(Answer).where(Answer.ticket_id == ticket_id).order_by(Answer.answer_time.desc())
            )
            last_answer = result.scalars().first()
            if last_answer:
                await message.bot.send_message(last_answer.telegram_id,
                                               f"Тикет №{ticket_id} получил ответ:\n\n{answer_text}")

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📋 Вернуться к тикету",
                                      callback_data=f"view_user_ticket_{ticket.ticket_id}")],
                [InlineKeyboardButton(text="📂 Вернуться к списку тикетов", callback_data="return_to_user_tickets")]
            ]
        )

        await message.answer("✅ Ваш ответ был успешно отправлен.", reply_markup=keyboard)
        await state.set_state(UserStates.AUTHENTICATED_USER)

    except Exception as e:
        logging.error(
            f"Ошибка при сохранении ответа на тикет {data['ticket_id']} пользователем {message.from_user.id}: {e}")
        await message.answer("❌ Произошла ошибка при обработке вашего запроса.")
        await state.set_state(UserStates.VIEW_TICKET)

@router.callback_query(lambda c: c.data == 'return_to_user_tickets', StateFilter(UserStates.VIEW_TICKET))
async def return_to_user_tickets(callback_query: CallbackQuery, state: FSMContext):
    user_id = callback_query.from_user.id  # Получаем ID пользователя из callback_query
    logging.info(f"Возврат в меню для пользователя с ID: {user_id}")  # Логируем ID пользователя

    await state.set_state(UserStates.AUTHENTICATED_USER)
    await show_user_tickets(callback_query.message, user_id)  # Передаем ID пользователя в show_user_tickets

@router.callback_query(lambda c: c.data.startswith('close_user_ticket_'), StateFilter(UserStates.VIEW_TICKET))
async def close_user_ticket_handler(callback_query: CallbackQuery, state: FSMContext):
    try:
        ticket_id = int(callback_query.data.split('_')[3])
        async with async_session() as session:
            result = await session.execute(select(Ticket).where(Ticket.ticket_id == ticket_id))
            ticket = result.scalars().first()

            if ticket:
                ticket.active = False
                ticket.closed_by_user = True
                await session.commit()
                await callback_query.message.edit_text("🔒 Тикет был закрыт.")
                await state.set_state(UserStates.AUTHENTICATED_USER)
            else:
                await callback_query.message.edit_text("❌ Тикет не найден.")
    except Exception as e:
        logging.error(f"Ошибка при закрытии тикета {ticket_id} пользователем {callback_query.from_user.id}: {e}")
        await callback_query.message.edit_text("❌ Произошла ошибка при обработке вашего запроса. Попробуйте позже.")
        await state.set_state(UserStates.AUTHENTICATED_USER)

@router.message(Command(commands=['showclosedtickets']), StateFilter(UserStates.AUTHENTICATED_USER))
async def show_closed_tickets_handler(message: types.Message):
    user_id = message.from_user.id
    await show_user_closed_tickets(message, user_id)

async def show_user_closed_tickets(message: types.Message, user_id: int):
    logging.info(f"Запрашиваем закрытые тикеты для пользователя: {user_id}")

    tickets = await get_user_closed_tickets(user_id)

    if not tickets:
        await message.answer("🔴 У вас нет закрытых тикетов.")
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[])

    for ticket in tickets:
        async with async_session() as session:
            result = await session.execute(
                select(Question).where(Question.ticket_id == ticket.ticket_id).order_by(Question.creation_time)
            )
            question = result.scalars().first()
            subject = question.subject if question else "Без темы"

            button_text = f"Тикет {ticket.ticket_id}: {subject}"
            keyboard.inline_keyboard.append([InlineKeyboardButton(text=button_text, callback_data=f"view_user_closed_ticket_{ticket.ticket_id}")])


    await message.answer("📂 Закрытые вами тикеты:", reply_markup=keyboard)
    logging.info(f"Пользователь {message.from_user.id} запросил закрытые тикеты.")

@router.callback_query(lambda c: c.data.startswith('view_user_closed_ticket_'), StateFilter(UserStates.AUTHENTICATED_USER))
async def view_user_closed_ticket(callback_query: CallbackQuery, state: FSMContext):
    logging.info(f"Просмотр закрытого тикета пользователем. Callback data: {callback_query.data}")
    try:
        ticket_id = int(callback_query.data.split('_')[4])
        history = await get_ticket_history(ticket_id)

        if not history:
            await callback_query.message.edit_text("📝 Нет сообщений в этом тикете.")
            logging.info(f"Тикет {ticket_id} не содержит сообщений.")
            return

        text = f"📋 **Ваш закрытый тикет №{ticket_id}**\n\n"
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

        # Проверка наличия медиафайлов
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

        # Создаем клавиатуру для закрытого тикета (без кнопок "Ответить" и "Закрыть тикет")
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Вернуться", callback_data="return_to_user_closed_tickets")]
            ]
        )

        # Добавляем кнопку для скачивания медиа, если файлы есть
        if has_media_files:
            keyboard.inline_keyboard.insert(1, [InlineKeyboardButton(text="📥 Скачать медиа", callback_data=f"download_media_{ticket_id}")])

        await callback_query.message.answer(text, parse_mode="HTML", reply_markup=keyboard)
        logging.info(f"Пользователю показан закрытый тикет {ticket_id}.")
        await state.update_data(ticket_id=ticket_id, ticket_text=text)
        await state.set_state(UserStates.VIEW_TICKET)

    except Exception as e:
        logging.error(f"Ошибка при просмотре закрытого тикета пользователем {callback_query.from_user.id}: {e}")
        await callback_query.message.edit_text("❌ Произошла ошибка при обработке вашего запроса. Попробуйте позже.")

@router.callback_query(lambda c: c.data == 'return_to_user_closed_tickets', StateFilter(UserStates.VIEW_TICKET))
async def return_to_user_closed_tickets(callback_query: CallbackQuery, state: FSMContext):
    user_id = callback_query.from_user.id  # Получаем ID пользователя из callback_query
    logging.info(f"Возврат к списку закрытых тикетов для пользователя с ID: {user_id}")  # Логируем ID пользователя

    await state.set_state(UserStates.AUTHENTICATED_USER)  # Устанавливаем состояние пользователя

    tickets = await get_user_closed_tickets(user_id)  # Получаем закрытые тикеты пользователя

    if not tickets:
        await callback_query.message.answer("🔴 У вас нет закрытых тикетов.")
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[])

    for ticket in tickets:
        async with async_session() as session:
            # Получаем первый вопрос для отображения темы тикета
            result = await session.execute(
                select(Question).where(Question.ticket_id == ticket.ticket_id).order_by(Question.creation_time)
            )
            question = result.scalars().first()
            subject = question.subject if question else "Без темы"

            # Формируем текст кнопки
            button_text = f"Тикет {ticket.ticket_id}: {subject}"
            keyboard.inline_keyboard.append(
                [InlineKeyboardButton(text=button_text, callback_data=f"view_user_closed_ticket_{ticket.ticket_id}")])

    await callback_query.message.answer("📂 Ваши закрытые тикеты:", reply_markup=keyboard)
    logging.info(f"Пользователь {callback_query.from_user.id} запросил свои закрытые тикеты.")