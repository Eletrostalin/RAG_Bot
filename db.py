import logging
import os
from datetime import datetime

from aiogram import types
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker, selectinload
from models import Base, User, Ticket, Question, Answer, Migration, MediaFile
from sqlalchemy.future import select
from sqlalchemy.sql import text
from config import DATABASE_URL
from utils.s3_utils import upload_to_s3

# Создаём асинхронный движок для работы с базой данных
engine = create_async_engine(DATABASE_URL, echo=True)

# Настройка асинхронной сессии
async_session = sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False
)


async def init_db():
    """
    Инициализирует базу данных при запуске приложения.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logging.info("Database initialized successfully.")


async def check_tables_exist() -> bool:
    """
    Проверяет наличие таблицы миграций в базе данных.

    Returns:
        bool: True, если таблица существует, иначе False.
    """
    async with async_session() as session:
        async with session.begin():
            result = await session.execute(
                text("SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'migrations');")
            )
            return result.scalar()


async def apply_migrations():
    """
    Применяет новые миграции из папки migrations к базе данных.
    """
    migrations_folder = 'migrations'

    # Проверка наличия таблиц в базе данных
    tables_exist = await check_tables_exist()

    async with async_session() as session:
        if tables_exist:
            result = await session.execute(select(Migration.migration_name))
            applied_migrations = set(row[0] for row in result.all())
        else:
            applied_migrations = set()
            logging.info("Таблицы не найдены. Применение последней миграции.")

        all_migrations = [f for f in os.listdir(migrations_folder) if f.endswith('.sql')]
        new_migrations = [m for m in all_migrations if m not in applied_migrations]
        new_migrations.sort()

        if new_migrations:
            logging.info(f"Найдено {len(new_migrations)} новых миграций: {new_migrations}")
            async with engine.connect() as conn:
                async with conn.begin():
                    try:
                        for migration in new_migrations:
                            with open(os.path.join(migrations_folder, migration), 'r', encoding='utf-8') as file:
                                sql_commands = file.read()
                            for command in sql_commands.split(';'):
                                command = command.strip()
                                if command:
                                    logging.info(f"Применение SQL команды:\n{command}")
                                    await conn.execute(text(command))
                        await conn.commit()

                        for migration in new_migrations:
                            session.add(Migration(migration_name=migration))
                        await session.commit()
                        logging.info(f"Миграции {new_migrations} успешно применены.")

                    except Exception as e:
                        logging.error(f"Ошибка при применении миграции: {e}")
                        await conn.rollback()
        else:
            logging.info("Новые миграции отсутствуют.")


async def get_user_by_telegram_id(telegram_id: int) -> User:
    """
    Получает пользователя по telegram_id.

    Args:
        telegram_id (int): ID пользователя в Telegram.

    Returns:
        User: Объект пользователя, если найден.
    """
    async with async_session() as session:
        result = await session.execute(select(User).where(User.telegram_id == telegram_id))
        user = result.scalars().first()
        logging.info(f"Получен пользователь с telegram ID: {user}")
        return user


async def get_active_tickets(offset: int = 0, limit: int = 10) -> list[Ticket]:
    """
    Получает список активных тикетов с возможностью постраничного вывода.

    Args:
        offset (int): Смещение, с какого тикета начинать.
        limit (int): Количество тикетов для отображения.

    Returns:
        list[Ticket]: Список активных тикетов.
    """
    async with async_session() as session:
        result = await session.execute(
            select(Ticket).where(Ticket.active == True).order_by(Ticket.last_updated.desc()).offset(offset).limit(limit)
        )
        tickets = result.scalars().all()

        for ticket in tickets:
            result = await session.execute(
                select(Answer).where(Answer.ticket_id == ticket.ticket_id).order_by(Answer.answer_time.desc())
            )
            last_answer = result.scalars().first()
            if last_answer:
                result = await session.execute(select(User).where(User.telegram_id == last_answer.telegram_id))
                admin = result.scalars().first()
                ticket.last_admin_name = admin.username if admin else "Админ"
            else:
                ticket.last_admin_name = "Админ"

        logging.info(f"Получены активные тикеты: {tickets}")
        return tickets


async def get_ticket_history(ticket_id: int) -> list:
    """
    Получает историю сообщений для тикета по его ID.

    Args:
        ticket_id (int): ID тикета.

    Returns:
        list: История сообщений и ответов для тикета.
    """
    async with async_session() as session:
        questions = await session.execute(
            select(Question).where(Question.ticket_id == ticket_id).options(selectinload(Question.ticket))
        )
        answers = await session.execute(
            select(Answer).where(Answer.ticket_id == ticket_id).options(selectinload(Answer.ticket))
        )

        questions = questions.scalars().all()
        answers = answers.scalars().all()

        history = sorted(questions + answers, key=lambda x: x.creation_time)
        logging.info(f"История тикета {ticket_id}: {history}")
        return history


async def close_ticket(ticket_id: int):
    """
    Закрывает тикет, устанавливая его как неактивный.

    Args:
        ticket_id (int): ID тикета, который нужно закрыть.
    """
    async with async_session() as session:
        result = await session.execute(select(Ticket).where(Ticket.ticket_id == ticket_id))
        ticket = result.scalars().first()
        if ticket:
            ticket.active = False
            await session.commit()
            logging.info(f"Тикет {ticket_id} закрыт.")
        else:
            logging.warning(f"Тикет {ticket_id} не найден.")


async def close_ticket_by_admin(ticket_id: int):
    """
    Закрывает тикет от имени администратора.

    Args:
        ticket_id (int): ID тикета для закрытия.
    """
    async with async_session() as session:
        result = await session.execute(select(Ticket).where(Ticket.ticket_id == ticket_id))
        ticket = result.scalars().first()
        if ticket:
            ticket.active = False
            await session.commit()
            logging.info(f"Администратор закрыл тикет {ticket_id}.")
        else:
            logging.warning(f"Тикет {ticket_id} не найден.")


async def close_ticket_by_user(ticket_id: int):
    """
    Закрытие или повторное открытие тикета пользователем.

    Args:
        ticket_id (int): ID тикета для обновления статуса.
    """
    async with async_session() as session:
        result = await session.execute(select(Ticket).where(Ticket.ticket_id == ticket_id))
        ticket = result.scalars().first()
        if ticket:
            ticket.active = not ticket.active  # Меняем статус активности
            await session.commit()
            logging.info(f"Пользователь изменил статус тикета {ticket_id}.")
        else:
            logging.warning(f"Тикет {ticket_id} не найден.")


async def get_user_tickets(user_id: int) -> list[Ticket]:
    """
    Получает все открытые тикеты пользователя.

    Args:
        user_id (int): ID пользователя в Telegram.

    Returns:
        list[Ticket]: Список тикетов, открытых пользователем.
    """
    async with async_session() as session:
        result = await session.execute(
            select(Ticket)
            .where(Ticket.telegram_id == user_id)
            .where(Ticket.closed_by_user == False)  # Фильтруем незакрытые тикеты
        )
        tickets = result.scalars().all()
        logging.info(f"Получены тикеты пользователя {user_id}: {tickets}")
        return tickets


async def get_closed_tickets() -> list[Ticket]:
    """
    Получает все закрытые тикеты.

    Returns:
        list[Ticket]: Список закрытых тикетов.
    """
    async with async_session() as session:
        result = await session.execute(select(Ticket).where(Ticket.active == False))
        tickets = result.scalars().all()
        logging.info(f"Получены закрытые тикеты: {tickets}")
        return tickets


async def add_question(user_id: int, question_text: str, subject: str, media: list = None,
                       from_user: types.User = None):
    """
    Добавляет вопрос (тикет) от пользователя в базу данных, создавая тикет и, при необходимости, прикрепляет медиафайлы.

    Args:
        user_id (int): ID пользователя в Telegram.
        question_text (str): Текст вопроса.
        subject (str): Тема вопроса.
        media (list, optional): Список медиафайлов, прикрепляемых к вопросу.
        from_user (types.User, optional): Информация о пользователе из Telegram.
    """
    async with async_session() as session:
        # Проверка существования пользователя
        result = await session.execute(select(User).where(User.telegram_id == user_id))
        user = result.scalars().first()

        # Если пользователь не найден, создаем нового
        if not user:
            username = from_user.username if from_user and from_user.username else "unknown_user"
            full_name = f"{from_user.first_name or ''} {from_user.last_name or ''}".strip() if from_user else "Неизвестно"
            new_user = User(telegram_id=user_id, username=username, full_name=full_name, is_admin=False)
            session.add(new_user)
            await session.commit()
            logging.info(f"Добавлен новый пользователь с telegram_id {user_id}.")
        else:
            updated = False
            if from_user and (
                    user.username != from_user.username or user.full_name != f"{from_user.first_name or ''} {from_user.last_name or ''}".strip()):
                user.username = from_user.username
                user.full_name = f"{from_user.first_name or ''} {from_user.last_name or ''}".strip()
                updated = True
            if updated:
                await session.commit()
                logging.info(f"Данные пользователя с telegram_id {user_id} обновлены.")

        # Создание тикета и вопроса
        ticket = Ticket(telegram_id=user_id, creation_time=datetime.utcnow(), last_updated=datetime.utcnow())
        session.add(ticket)
        await session.commit()

        new_question = Question(telegram_id=user_id, ticket_id=ticket.ticket_id, text=question_text, subject=subject)
        session.add(new_question)
        await session.commit()

        ticket.last_updated = datetime.utcnow()

        # Работа с медиафайлами
        if media:
            for media_file in media:
                file_content = media_file.get('file')  # Предполагается BytesIO
                filename = media_file.get('filename')
                file_url = await upload_to_s3(file_content, "fdfd", filename)
                file_type = 'image' if media_file.get('is_image') else 'video'
                media_entry = MediaFile(file_url=file_url, file_type=file_type, filename=filename,
                                        question_id=new_question.question_id, ticket_id=ticket.ticket_id)
                session.add(media_entry)

        await session.commit()
        logging.info(f"Добавлен вопрос с тикетом {ticket.ticket_id}.")
        return new_question


async def add_question_to_ticket(user_id: int, ticket_id: int, question_text: str, subject: str,
                                 media_files: list = None):
    """
    Добавляет новый вопрос в существующий тикет.

    Args:
        user_id (int): ID пользователя в Telegram.
        ticket_id (int): ID тикета.
        question_text (str): Текст вопроса.
        subject (str): Тема вопроса.
        media_files (list, optional): Список медиафайлов.

    Returns:
        Question: Созданный вопрос.
    """
    async with async_session() as session:
        result = await session.execute(select(Ticket).where(Ticket.ticket_id == ticket_id))
        ticket = result.scalars().first()

        if not ticket:
            raise ValueError(f"Тикет с id {ticket_id} не найден.")

        new_question = Question(telegram_id=user_id, ticket_id=ticket_id, text=question_text, subject=subject,
                                creation_time=datetime.utcnow())
        session.add(new_question)

        if media_files:
            for media in media_files:
                file_content = media.get('file')
                filename = media.get('filename')
                file_url = await upload_to_s3(file_content, "fdfd", filename)
                file_type = 'image' if media.get('is_image') else 'video'
                media_entry = MediaFile(file_url=file_url, file_type=file_type, filename=filename,
                                        question_id=new_question.question_id, ticket_id=ticket.ticket_id)
                session.add(media_entry)

        ticket.active = True
        ticket.last_updated = datetime.utcnow()

        await session.commit()
        logging.info(f"Добавлен новый вопрос для тикета {ticket_id}.")
        return new_question


async def add_answer(admin_id: int, ticket_id: int, answer_text: str, media: list = None, from_user: types.User = None):
    """
    Добавляет ответ от администратора в существующий тикет, а также обрабатывает прикрепленные медиафайлы.

    Args:
        admin_id (int): ID администратора в Telegram.
        ticket_id (int): ID тикета, в который добавляется ответ.
        answer_text (str): Текст ответа.
        media (list, optional): Список медиафайлов (если есть).
        from_user (types.User, optional): Информация о пользователе из Telegram (если передана).

    Returns:
        tuple: Возвращает добавленный ответ и тикет, к которому он относится.
    """
    async with async_session() as session:
        # Проверка существования администратора в базе данных
        result = await session.execute(select(User).where(User.telegram_id == admin_id))
        user = result.scalars().first()

        # Если пользователь не найден, создаём нового
        if not user:
            username = from_user.username if from_user and from_user.username else "unknown_user"
            full_name = f"{from_user.first_name or ''} {from_user.last_name or ''}".strip() if from_user else "Неизвестно"
            new_user = User(
                telegram_id=admin_id,
                username=username,
                full_name=full_name,
                is_admin=True  # Устанавливаем статус администратора
            )
            session.add(new_user)
            await session.commit()
            logging.info(f"Добавлен новый администратор с telegram_id {admin_id}.")
        else:
            # Обновляем данные пользователя, если это необходимо
            username = from_user.username if from_user and from_user.username else user.username
            full_name = f"{from_user.first_name or ''} {from_user.last_name or ''}".strip() if from_user else user.full_name
            user.username = username
            user.full_name = full_name
            session.add(user)
            await session.commit()

        # Создание нового ответа
        new_answer = Answer(ticket_id=ticket_id, telegram_id=admin_id, text=answer_text)
        session.add(new_answer)

        # Обновляем время последнего изменения тикета
        result = await session.execute(select(Ticket).where(Ticket.ticket_id == ticket_id))
        ticket = result.scalars().first()
        if ticket:
            ticket.last_updated = datetime.utcnow()
            await session.commit()
            logging.info(f"Добавлен ответ администратора в тикет {ticket_id}.")

            # Обработка медиафайлов, если они есть
            if media:
                for media_file in media:
                    file_url = await upload_to_s3(media_file['file'], "fdfd", media_file['filename'])
                    file_type = 'image' if media_file['is_image'] else 'video'
                    media_entry = MediaFile(
                        file_url=file_url,
                        file_type=file_type,
                        filename=media_file['filename'],
                        answer_id=new_answer.answer_id,
                        ticket_id=ticket.ticket_id
                    )
                    session.add(media_entry)

            await session.commit()
            return new_answer, ticket
        else:
            logging.warning(f"Тикет {ticket_id} не найден.")
            return None, None


async def get_user_closed_tickets(user_id: int) -> list[Ticket]:
    """
    Получает список закрытых тикетов пользователя.

    Args:
        user_id (int): ID пользователя в Telegram.

    Returns:
        list[Ticket]: Список закрытых тикетов пользователя.
    """
    async with async_session() as session:
        result = await session.execute(
            select(Ticket).where(Ticket.telegram_id == user_id, Ticket.closed_by_user == True)
        )
        tickets = result.scalars().all()
        logging.info(f"Получены закрытые тикеты пользователя {user_id}: {tickets}")
        return tickets

