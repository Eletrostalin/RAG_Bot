import logging
from aiogram import types, Router
from aiogram.fsm.context import FSMContext
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from states import AdminStates
from db import get_closed_tickets, get_ticket_history, async_session
from models import Question, User
from aiogram.filters import Command, StateFilter
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

router = Router()


@router.message(Command(commands=['getclosedticket']), StateFilter(AdminStates.AUTHENTICATED_ADMIN))
async def get_closed_tickets_handler(message: types.Message, state: FSMContext):
    """
    Обработчик команды администратора для получения списка закрытых тикетов.
    """
    try:
        tickets = await get_closed_tickets()

        if not tickets:
            await message.answer("🔴 Нет закрытых тикетов.")
            return

        keyboard = InlineKeyboardMarkup(inline_keyboard=[])

        for ticket in tickets:
            async with async_session() as session:
                # Получаем первый вопрос для тикета
                result = await session.execute(
                    select(Question).where(Question.ticket_id == ticket.ticket_id).order_by(Question.creation_time)
                )
                question = result.scalars().first()
                subject = question.subject if question else "Без темы"

            # Добавляем тему на кнопку
            button_text = f"📋 Тикет {ticket.ticket_id}: {subject}"
            keyboard.inline_keyboard.append([InlineKeyboardButton(text=button_text, callback_data=f"view_ticket_{ticket.ticket_id}")])

        keyboard.inline_keyboard.append([InlineKeyboardButton(text="🏠 Вернуться", callback_data="return_to_authorized")])

        await message.answer("📂 Закрытые тикеты:", reply_markup=keyboard)
        logging.info(f"Администратор {message.from_user.id} запросил закрытые тикеты.")
        await state.update_data(viewing_closed_tickets=True)
    except Exception as e:
        logging.error(f"Ошибка при запросе закрытых тикетов администратором {message.from_user.id}: {e}")
        await message.answer("❌ Произошла ошибка при обработке вашего запроса. Попробуйте позже.")


@router.callback_query(lambda c: c.data.startswith('view_ticket_'), StateFilter(AdminStates.AUTHENTICATED_ADMIN))
async def view_ticket(callback_query: CallbackQuery, state: FSMContext):
    """
    Обработчик для просмотра конкретного тикета администратором.
    """
    try:
        ticket_id = int(callback_query.data.split('_')[2])
        history = await get_ticket_history(ticket_id)

        if not history:
            await callback_query.message.edit_text("📝 Нет сообщений в этом тикете.")
            return

        async with async_session() as session:
            # Получаем пользователя, создавшего тикет
            result = await session.execute(
                select(User).where(User.telegram_id == history[0].telegram_id)
            )
            user = result.scalars().first()

        text = ""
        for entry in history:
            async with async_session() as session:
                result = await session.execute(
                    select(User).where(User.telegram_id == entry.telegram_id)
                )
                user = result.scalars().first()

                user_display_name = user.full_name or user.username or "Неизвестно"

                entry_text = (
                    f"👤 **Имя:** {user_display_name}\n"
                    f"📅 **Дата:** {entry.creation_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                    f"📝 **{'Вопрос' if isinstance(entry, Question) else 'Ответ'}:**\n{entry.text}\n\n"
                )
                text += entry_text

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Вернуться к списку закрытых тикетов", callback_data="return_to_closed_tickets")]
            ]
        )

        await callback_query.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
        await state.update_data(ticket_id=ticket_id, ticket_text=text)
        await state.set_state(AdminStates.VIEW_TICKET)
    except Exception as e:
        logging.error(f"Ошибка при просмотре тикета {ticket_id} администратором {callback_query.from_user.id}: {e}")
        await callback_query.message.edit_text("❌ Произошла ошибка при обработке вашего запроса. Попробуйте позже.")


@router.callback_query(lambda c: c.data == 'return_to_closed_tickets', StateFilter(AdminStates.VIEW_TICKET))
async def return_to_closed_tickets(callback_query: CallbackQuery, state: FSMContext):
    """
    Обработчик возврата к списку закрытых тикетов.
    """
    try:
        tickets = await get_closed_tickets()

        if not tickets:
            await callback_query.message.edit_text("🔴 Нет закрытых тикетов.")
            return

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=f"📋 Тикет {ticket.ticket_id}", callback_data=f"view_ticket_{ticket.ticket_id}")]
                for ticket in tickets
            ]
        )
        keyboard.inline_keyboard.append([InlineKeyboardButton(text="🏠 Вернуться", callback_data="return_to_authorized")])

        await callback_query.message.edit_text("📂 Закрытые тикеты:", reply_markup=keyboard)
        await state.set_state(AdminStates.AUTHENTICATED_ADMIN)
        logging.info(f"Администратор {callback_query.from_user.id} вернулся к списку закрытых тикетов.")
    except Exception as e:
        logging.error(f"Ошибка при возврате к списку закрытых тикетов администратором {callback_query.from_user.id}: {e}")
        await callback_query.message.edit_text("❌ Произошла ошибка при обработке вашего запроса. Попробуйте позже.")


@router.callback_query(lambda c: c.data == 'return_to_authorized', StateFilter(AdminStates.AUTHENTICATED_ADMIN))
async def return_to_authorized(callback_query: CallbackQuery, state: FSMContext):
    """
    Обработчик возврата в главное меню администратора.
    """
    try:
        await callback_query.message.edit_text("🏠 Вы вернулись в меню администратора.")
        await state.set_state(AdminStates.AUTHENTICATED_ADMIN)
        logging.info(f"Администратор {callback_query.from_user.id} вернулся в меню.")
    except Exception as e:
        logging.error(f"Ошибка при возврате в меню администратором {callback_query.from_user.id}: {e}")
        await callback_query.message.edit_text("❌ Произошла ошибка при обработке вашего запроса. Попробуйте позже.")