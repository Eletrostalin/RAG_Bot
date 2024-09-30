
# Code from db.py
import logging
import os
from datetime import datetime

from aiogram import types
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker, selectinload
from models import Base, User, Ticket, Question, Answer, Migration, MediaFile
from sqlalchemy.future import select
from sqlalchemy.sql import text, exists
from config import DATABASE_URL
from utils.s3_utils import upload_to_s3

engine = create_async_engine(DATABASE_URL, echo=True)
async_session = sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False
)

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logging.info("Database initialized successfully.")

async def check_tables_exist() -> bool:

    async with async_session() as session:
        async with session.begin():
            # Check if the 'migrations' table exists in the database
            result = await session.execute(
                text("SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'migrations');")
            )
            return result.scalar()

async def apply_migrations():
    """
    Применяет новые миграции из папки migrations к базе данных, используя SQLAlchemy.
    """
    migrations_folder = 'migrations'

    # Проверка наличия таблиц в базе данных
    tables_exist = await check_tables_exist()

    async with async_session() as session:
        # Получение списка применённых миграций из базы данных
        if tables_exist:
            result = await session.execute(select(Migration.migration_name))
            applied_migrations = set(row[0] for row in result.all())
        else:
            applied_migrations = set()
            logging.info("Таблицы не найдены в базе данных. Применение последней миграции.")

        # Получение списка всех SQL файлов в папке миграций
        all_migrations = [f for f in os.listdir(migrations_folder) if f.endswith('.sql')]

        # Фильтрация новых миграций, которые ещё не были применены
        new_migrations = [m for m in all_migrations if m not in applied_migrations]

        # Сортировка миграций по имени файла для их применения в порядке
        new_migrations.sort()

        if new_migrations:
            logging.info(f"Найдено {len(new_migrations)} новых миграций: {new_migrations}")

            async with engine.connect() as conn:  # Асинхронное подключение к базе данных
                async with conn.begin():  # Начало транзакции
                    try:
                        for migration in new_migrations:
                            migration_file_path = os.path.join(migrations_folder, migration)

                            # Открытие файла миграции и чтение SQL команд
                            with open(migration_file_path, 'r', encoding='utf-8') as migration_file:
                                sql_commands = migration_file.read()

                            # Разделение команд по ';' и выполнение каждой команды отдельно
                            for command in sql_commands.split(';'):
                                command = command.strip()
                                if command:  # Игнорируем пустые строки
                                    logging.info(f"Применение SQL команды:\n{command}")
                                    await conn.execute(text(command))  # Выполнение SQL команды

                        await conn.commit()

                        # Добавление миграций в базу данных после выполнения всех команд
                        async with engine.connect() as inner_conn:
                            async with inner_conn.begin():
                                for migration in new_migrations:
                                    new_migration = Migration(migration_name=migration)
                                    session.add(new_migration)
                                await session.commit()
                                logging.info(f"Миграции {new_migrations} успешно применены.")

                    except Exception as e:
                        logging.error(f"Ошибка при применении миграции: {e}")
                        await conn.rollback()  # Откат транзакции при ошибке
                    else:
                        await conn.commit()  # Подтверждение транзакции
        else:
            logging.info("Новые миграции отсутствуют.")

async def get_user_by_telegram_id(telegram_id: int) -> User:
    async with async_session() as session:
        result = await session.execute(select(User).where(User.telegram_id == telegram_id))
        user = result.scalars().first()
        logging.info(f"Retrieved user by telegram ID: {user}")
        return user

async def get_active_tickets(offset: int = 0, limit: int = 10) -> list[Ticket]:
    async with async_session() as session:
        result = await session.execute(
            select(Ticket).where(Ticket.active == True).order_by(Ticket.last_updated.desc()).offset(offset).limit(limit)
        )
        tickets = result.scalars().all()

        # Получение имени последнего ответившего администратора для каждого тикета
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

        logging.info(f"Retrieved active tickets: {tickets}")
        return tickets

async def get_questions_by_ticket_id(ticket_id: int) -> list[Question]:
    async with async_session() as session:
        result = await session.execute(select(Question).where(Question.ticket_id == ticket_id))
        questions = result.scalars().all()
        logging.info(f"Retrieved questions for ticket {ticket_id}: {questions}")
        return questions

async def get_ticket_history(ticket_id: int) -> list:
    async with async_session() as session:
        questions = await session.execute(
            select(Question)
            .where(Question.ticket_id == ticket_id)
            .options(selectinload(Question.ticket))
        )
        answers = await session.execute(
            select(Answer)
            .where(Answer.ticket_id == ticket_id)
            .options(selectinload(Answer.ticket))
        )

        questions = questions.scalars().all()
        answers = answers.scalars().all()

        for question in questions:
            question.creation_time = question.creation_time

        for answer in answers:
            answer.creation_time = answer.answer_time

        history = sorted(questions + answers, key=lambda x: x.creation_time)
        logging.info(f"Retrieved history for ticket {ticket_id}: {history}")
        return history

async def close_ticket(ticket_id: int):
    async with async_session() as session:
        result = await session.execute(select(Ticket).where(Ticket.ticket_id == ticket_id))
        ticket = result.scalars().first()
        if ticket:
            ticket.active = False
            await session.commit()
            logging.info(f"Closed ticket {ticket_id}")
        else:
            logging.warning(f"Ticket {ticket_id} not found")

async def close_ticket_by_admin(ticket_id: int):
    async with async_session() as session:
        result = await session.execute(select(Ticket).where(Ticket.ticket_id == ticket_id))
        ticket = result.scalars().first()
        if ticket:
            ticket.active = False
            await session.commit()
            logging.info(f"Admin closed ticket {ticket_id}")
        else:
            logging.warning(f"Ticket {ticket_id} not found")

async def close_ticket_by_user(ticket_id: int):
    async with async_session() as session:
        result = await session.execute(select(Ticket).where(Ticket.ticket_id == ticket_id))
        ticket = result.scalars().first()
        if ticket:
            if ticket.active:
                ticket.active = False
            else:
                ticket.active = True
            await session.commit()
            logging.info(f"User closed ticket {ticket_id}")
        else:
            logging.warning(f"Ticket {ticket_id} not found")

async def get_user_tickets(user_id: int) -> list[Ticket]:
    async with async_session() as session:
        result = await session.execute(
            select(Ticket)
            .where(Ticket.telegram_id == user_id)
            .where(Ticket.closed_by_user == False)  # Фильтруем тикеты, которые пользователь еще не закрыл
        )
        tickets = result.scalars().all()
        return tickets
async def get_closed_tickets() -> list[Ticket]:
    async with async_session() as session:
        result = await session.execute(select(Ticket).where(Ticket.active == False))
        tickets = result.scalars().all()
        logging.info(f"Retrieved closed tickets: {tickets}")
        return tickets

# Эта функция для добавления вопроса из общего чата
async def add_question(user_id: int, question_text: str, subject: str, media: list = None, from_user: types.User = None):
    async with async_session() as session:
        # Проверяем, существует ли пользователь в базе данных
        result = await session.execute(select(User).where(User.telegram_id == user_id))
        user = result.scalars().first()

        # Если пользователь не найден, добавляем его
        if not user:
            # Извлекаем данные пользователя из from_user (если переданы)
            username = from_user.username if from_user and from_user.username else "unknown_user"
            full_name = f"{from_user.first_name or ''} {from_user.last_name or ''}".strip() if from_user else "Неизвестно"

            # Создаем запись для нового пользователя
            new_user = User(
                telegram_id=user_id,
                username=username,
                full_name=full_name,
                is_admin=False  # Это обычный пользователь
            )
            session.add(new_user)
            await session.commit()
            logging.info(f"Добавлен новый пользователь с telegram_id {user_id}.")

        # Если пользователь найден, проверяем и обновляем его данные
        else:
            updated = False
            if from_user:
                if user.username != from_user.username:
                    user.username = from_user.username
                    updated = True
                if user.full_name != f"{from_user.first_name or ''} {from_user.last_name or ''}".strip():
                    user.full_name = f"{from_user.first_name or ''} {from_user.last_name or ''}".strip()
                    updated = True
            if updated:
                await session.commit()
                logging.info(f"Данные пользователя с telegram_id {user_id} обновлены.")

        # Создание нового тикета
        ticket = Ticket(telegram_id=user_id, creation_time=datetime.utcnow(), last_updated=datetime.utcnow())
        session.add(ticket)
        await session.commit()

        # Создание нового вопроса
        new_question = Question(telegram_id=user_id, ticket_id=ticket.ticket_id, text=question_text, subject=subject)
        session.add(new_question)
        await session.commit()

        # Обновление времени последнего изменения тикета
        ticket.last_updated = datetime.utcnow()

        # Работа с медиафайлами, если они есть
        if media:
            for media_file in media:
                # Получаем содержимое файла и имя
                file_content = media_file.get('file')  # Это уже объект BytesIO
                filename = media_file.get('filename')

                # Асинхронная загрузка файла в S3
                file_url = await upload_to_s3(file_content, "fdfd", filename)

                # Добавление записи о медиафайле в базу данных
                file_type = 'image' if media_file.get('is_image') else 'video'
                media_entry = MediaFile(
                    file_url=file_url,
                    file_type=file_type,
                    filename=filename,
                    question_id=new_question.question_id,
                    ticket_id=ticket.ticket_id
                )
                session.add(media_entry)

        await session.commit()

        logging.info(f"Добавлен вопрос с тикетом {ticket.ticket_id}.")
        return new_question

# Эта функция для добавления вопроса (ответа) от пользователя на уже существующий тикет
async def add_question_to_ticket(user_id: int, ticket_id: int, question_text: str, subject: str, media_files: list = None):
    async with async_session() as session:
        # Проверка существования тикета
        result = await session.execute(select(Ticket).where(Ticket.ticket_id == ticket_id))
        ticket = result.scalars().first()

        if not ticket:
            raise ValueError(f"Тикет с id {ticket_id} не найден.")

        # Создание нового вопроса для существующего тикета
        new_question = Question(
            telegram_id=user_id,
            ticket_id=ticket_id,
            text=question_text,
            subject=subject,
            creation_time=datetime.utcnow()
        )
        session.add(new_question)

        # Работа с медиафайлами, если они есть
        if media_files:
            for media in media_files:
                # Считаем, что у нас есть логика для загрузки файлов на S3 или другое хранилище
                file_content = media.get('file')  # Это уже объект BytesIO
                filename = media.get('filename')
                file_url = await upload_to_s3(file_content, "fdfd", filename)

                # Добавляем запись о медиафайле в базу данных
                file_type = 'image' if media.get('is_image') else 'video'
                media_entry = MediaFile(
                    file_url=file_url,
                    file_type=file_type,
                    filename=filename,
                    question_id=new_question.question_id,  # Связываем с вопросом
                    ticket_id=new_question.ticket_id
                )
                session.add(media_entry)

        # Обновляем тикет (например, активируем его снова)
        ticket.active = True
        ticket.last_updated = datetime.utcnow()

        await session.commit()

        logging.info(f"Добавлен новый вопрос для тикета {ticket_id}.")
        return new_question


async def add_answer(admin_id: int, ticket_id: int, answer_text: str, media: list = None, from_user: types.User = None):
    async with async_session() as session:
        # Проверяем, существует ли пользователь в базе данных
        result = await session.execute(select(User).where(User.telegram_id == admin_id))
        user = result.scalars().first()

        if not user:
            # Извлекаем данные пользователя из `from_user`
            username = from_user.username if from_user and from_user.username else "unknown_user"
            full_name = f"{from_user.first_name or ''} {from_user.last_name or ''}".strip() if from_user else "Неизвестно"

            new_user = User(
                telegram_id=admin_id,
                username=username,
                full_name=full_name,
                is_admin=True  # Указываем, что это администратор
            )
            session.add(new_user)
            await session.commit()
            logging.info(f"Добавлен новый администратор с telegram_id {admin_id}.")
        else:
            # Обновляем данные пользователя, если необходимо
            username = from_user.username if from_user and from_user.username else user.username
            full_name = f"{from_user.first_name or ''} {from_user.last_name or ''}".strip() if from_user else user.full_name

            user.username = username
            user.full_name = full_name
            session.add(user)
            await session.commit()

        # Создаём новый ответ
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
    async with async_session() as session:
        result = await session.execute(
            select(Ticket).where(Ticket.telegram_id == user_id, Ticket.closed_by_user == True)
        )
        tickets = result.scalars().all()
        logging.info(f"Retrieved closed tickets for user {user_id}: {tickets}")
        return tickets


# End of code from db.py

# Code from config.py
import os
import logging
from dotenv import load_dotenv


LOG_FORMAT = "%(levelname)s %(asctime)s - %(message)s"
# Настройка логирования только для консоли
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(logging.Formatter(LOG_FORMAT))

logging.basicConfig(
    level=logging.INFO,
    format=LOG_FORMAT,
    handlers=[console_handler]
)

logger = logging.getLogger()


# Загрузка переменных окружения из файла .env
load_dotenv()

TOKEN = os.getenv('TOKEN')
DATABASE_URL = os.getenv('DATABASE_URL')


ADMIN_IDS = [int(id) for id in os.getenv('ADMIN_IDS').split(',')]
CHAT_ID = int(os.getenv('CHAT_ID'))

# s7 хранилище
AWS_ACCESS_KEY_ID = os.getenv('AWS_ACCESS_KEY_ID')
AWS_SECRET_ACCESS_KEY = os.getenv('AWS_SECRET_ACCESS_KEY')
S3_ENDPOINT_URL = os.getenv('S3_ENDPOINT_URL')
S3_BUCKET_NAME = os.getenv('S3_BUCKET_NAME')

# Получаем переменные из окружения
IAM_TOKEN = os.getenv("IAM_TOKEN")
FOLDER_ID = os.getenv("FOLDER_ID")
RAG_API_URL = os.getenv("RAG_API_URL")
LLM_RAG_ENDPOINT = os.getenv("LLM_RAG_ENDPOINT")
TXT_PATH = os.getenv("TXT_PATH")
  # Укажите реальный путь к вашему текстовому файлу
# End of code from config.py

# Code from models.py
from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, DateTime, BigInteger
from sqlalchemy.orm import relationship
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime

Base = declarative_base()

class User(Base):
    __tablename__ = 'users'

    telegram_id = Column(BigInteger, primary_key=True, unique=True)
    username = Column(String(30))  # Уникальное имя пользователя в Telegram
    full_name = Column(String(100))  # Полное имя пользователя, необязательно
    is_admin = Column(Boolean, default=False)

    tickets = relationship('Ticket', back_populates='user')
    questions = relationship('Question', back_populates='user')
    answers = relationship('Answer', back_populates='user')

class Ticket(Base):
    __tablename__ = 'tickets'

    ticket_id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(BigInteger, ForeignKey('users.telegram_id'), nullable=False)
    creation_time = Column(DateTime, default=datetime.utcnow)
    completion_time = Column(DateTime)
    active = Column(Boolean, default=True)
    closed_by_user = Column(Boolean, default=False)  # Новое поле
    last_updated = Column(DateTime, default=datetime.utcnow)

    user = relationship('User', back_populates='tickets')
    questions = relationship('Question', back_populates='ticket')
    answers = relationship('Answer', back_populates='ticket')
    media_files = relationship('MediaFile', back_populates='ticket', cascade="all, delete-orphan")

class Question(Base):
    __tablename__ = 'questions'

    question_id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(BigInteger, ForeignKey('users.telegram_id'), nullable=False)
    creation_time = Column(DateTime, default=datetime.utcnow)
    ticket_id = Column(Integer, ForeignKey('tickets.ticket_id'))
    text = Column(String(3000))
    subject = Column(String(255))  # Тема вопроса

    user = relationship('User', back_populates='questions')
    ticket = relationship('Ticket', back_populates='questions')

    # Новое поле для связи с медиафайлами
    media_files = relationship('MediaFile', back_populates='question', cascade="all, delete-orphan")

class Answer(Base):
    __tablename__ = 'answers'

    answer_id = Column(Integer, primary_key=True, autoincrement=True)
    ticket_id = Column(Integer, ForeignKey('tickets.ticket_id'), nullable=False)
    telegram_id = Column(BigInteger, ForeignKey('users.telegram_id'), nullable=False)
    answer_time = Column(DateTime, default=datetime.utcnow)
    text = Column(String(3000))

    user = relationship('User', back_populates='answers')
    ticket = relationship('Ticket', back_populates='answers')

    # Новое поле для связи с медиафайлами
    media_files = relationship('MediaFile', back_populates='answer', cascade="all, delete-orphan")

class MediaFile(Base):
    __tablename__ = 'media_files'

    id = Column(Integer, primary_key=True)
    file_url = Column(String, nullable=False)
    file_type = Column(String, nullable=False)
    filename = Column(String, nullable=False)
    question_id = Column(Integer, ForeignKey('questions.question_id'), nullable=True)
    answer_id = Column(Integer, ForeignKey('answers.answer_id'), nullable=True)
    ticket_id = Column(Integer, ForeignKey('tickets.ticket_id'), nullable=True)  # Связь с тикетом

    # Определяем отношения с моделями Question, Answer и Ticket
    question = relationship("Question", back_populates="media_files", foreign_keys=[question_id])
    answer = relationship("Answer", back_populates="media_files", foreign_keys=[answer_id])
    ticket = relationship("Ticket", back_populates="media_files", foreign_keys=[ticket_id])  # Связь с моделью Ticket

class Migration(Base):
    __tablename__ = 'migrations'

    id = Column(Integer, primary_key=True, autoincrement=True)
    migration_name = Column(String(255), nullable=False, unique=True)
    applied_on = Column(DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<Migration(name={self.migration_name}, applied_on={self.applied_on})>"


# End of code from models.py

# Code from ai_script.py
import os

def gather_code_from_directory(directory, output_file, ignore_dirs=None):
    if ignore_dirs is None:
        ignore_dirs = {'.venv', 'postgres_data'}

    with open(output_file, 'w', encoding='utf-8') as outfile:
        for root, dirs, files in os.walk(directory):
            # Игнорируем указанные директории
            dirs[:] = [d for d in dirs if d not in ignore_dirs]
            for file in files:
                if file.endswith('.py'):
                    file_path = os.path.join(root, file)
                    with open(file_path, 'r', encoding='utf-8') as infile:
                        outfile.write(f"\n# Code from {file}\n")
                        outfile.write(infile.read())
                        outfile.write(f"\n# End of code from {file}\n")

if __name__ == "__main__":
    project_directory = os.getcwd()  # Текущая рабочая директория
    output_file_path = "combined_code.py"  # Имя выходного файла

    gather_code_from_directory(project_directory, output_file_path)
    print(f"Code from all .py files in {project_directory} has been combined into {output_file_path}")

# End of code from ai_script.py

# Code from combined_code.py

# Code from db.py
import logging
import os
from datetime import datetime

from aiogram import types
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker, selectinload
from models import Base, User, Ticket, Question, Answer, Migration, MediaFile
from sqlalchemy.future import select
from sqlalchemy.sql import text, exists
from config import DATABASE_URL
from utils.s3_utils import upload_to_s3

engine = create_async_engine(DATABASE_URL, echo=True)
async_session = sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False
)

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logging.info("Database initialized successfully.")

async def check_tables_exist() -> bool:

    async with async_session() as session:
        async with session.begin():
            # Check if the 'migrations' table exists in the database
            result = await session.execute(
                text("SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'migrations');")
            )
            return result.scalar()

async def apply_migrations():
    """
    Применяет новые миграции из папки migrations к базе данных, используя SQLAlchemy.
    """
    migrations_folder = 'migrations'

    # Проверка наличия таблиц в базе данных
    tables_exist = await check_tables_exist()

    async with async_session() as session:
        # Получение списка применённых миграций из базы данных
        if tables_exist:
            result = await session.execute(select(Migration.migration_name))
            applied_migrations = set(row[0] for row in result.all())
        else:
            applied_migrations = set()
            logging.info("Таблицы не найдены в базе данных. Применение последней миграции.")

        # Получение списка всех SQL файлов в папке миграций
        all_migrations = [f for f in os.listdir(migrations_folder) if f.endswith('.sql')]

        # Фильтрация новых миграций, которые ещё не были применены
        new_migrations = [m for m in all_migrations if m not in applied_migrations]

        # Сортировка миграций по имени файла для их применения в порядке
        new_migrations.sort()

        if new_migrations:
            logging.info(f"Найдено {len(new_migrations)} новых миграций: {new_migrations}")

            async with engine.connect() as conn:  # Асинхронное подключение к базе данных
                async with conn.begin():  # Начало транзакции
                    try:
                        for migration in new_migrations:
                            migration_file_path = os.path.join(migrations_folder, migration)

                            # Открытие файла миграции и чтение SQL команд
                            with open(migration_file_path, 'r', encoding='utf-8') as migration_file:
                                sql_commands = migration_file.read()

                            # Разделение команд по ';' и выполнение каждой команды отдельно
                            for command in sql_commands.split(';'):
                                command = command.strip()
                                if command:  # Игнорируем пустые строки
                                    logging.info(f"Применение SQL команды:\n{command}")
                                    await conn.execute(text(command))  # Выполнение SQL команды

                        await conn.commit()

                        # Добавление миграций в базу данных после выполнения всех команд
                        async with engine.connect() as inner_conn:
                            async with inner_conn.begin():
                                for migration in new_migrations:
                                    new_migration = Migration(migration_name=migration)
                                    session.add(new_migration)
                                await session.commit()
                                logging.info(f"Миграции {new_migrations} успешно применены.")

                    except Exception as e:
                        logging.error(f"Ошибка при применении миграции: {e}")
                        await conn.rollback()  # Откат транзакции при ошибке
                    else:
                        await conn.commit()  # Подтверждение транзакции
        else:
            logging.info("Новые миграции отсутствуют.")

async def get_user_by_telegram_id(telegram_id: int) -> User:
    async with async_session() as session:
        result = await session.execute(select(User).where(User.telegram_id == telegram_id))
        user = result.scalars().first()
        logging.info(f"Retrieved user by telegram ID: {user}")
        return user

async def get_active_tickets(offset: int = 0, limit: int = 10) -> list[Ticket]:
    async with async_session() as session:
        result = await session.execute(
            select(Ticket).where(Ticket.active == True).order_by(Ticket.last_updated.desc()).offset(offset).limit(limit)
        )
        tickets = result.scalars().all()

        # Получение имени последнего ответившего администратора для каждого тикета
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

        logging.info(f"Retrieved active tickets: {tickets}")
        return tickets

async def get_questions_by_ticket_id(ticket_id: int) -> list[Question]:
    async with async_session() as session:
        result = await session.execute(select(Question).where(Question.ticket_id == ticket_id))
        questions = result.scalars().all()
        logging.info(f"Retrieved questions for ticket {ticket_id}: {questions}")
        return questions

async def get_ticket_history(ticket_id: int) -> list:
    async with async_session() as session:
        questions = await session.execute(
            select(Question)
            .where(Question.ticket_id == ticket_id)
            .options(selectinload(Question.ticket))
        )
        answers = await session.execute(
            select(Answer)
            .where(Answer.ticket_id == ticket_id)
            .options(selectinload(Answer.ticket))
        )

        questions = questions.scalars().all()
        answers = answers.scalars().all()

        for question in questions:
            question.creation_time = question.creation_time

        for answer in answers:
            answer.creation_time = answer.answer_time

        history = sorted(questions + answers, key=lambda x: x.creation_time)
        logging.info(f"Retrieved history for ticket {ticket_id}: {history}")
        return history

async def close_ticket(ticket_id: int):
    async with async_session() as session:
        result = await session.execute(select(Ticket).where(Ticket.ticket_id == ticket_id))
        ticket = result.scalars().first()
        if ticket:
            ticket.active = False
            await session.commit()
            logging.info(f"Closed ticket {ticket_id}")
        else:
            logging.warning(f"Ticket {ticket_id} not found")

async def close_ticket_by_admin(ticket_id: int):
    async with async_session() as session:
        result = await session.execute(select(Ticket).where(Ticket.ticket_id == ticket_id))
        ticket = result.scalars().first()
        if ticket:
            ticket.active = False
            await session.commit()
            logging.info(f"Admin closed ticket {ticket_id}")
        else:
            logging.warning(f"Ticket {ticket_id} not found")

async def close_ticket_by_user(ticket_id: int):
    async with async_session() as session:
        result = await session.execute(select(Ticket).where(Ticket.ticket_id == ticket_id))
        ticket = result.scalars().first()
        if ticket:
            if ticket.active:
                ticket.active = False
            else:
                ticket.active = True
            await session.commit()
            logging.info(f"User closed ticket {ticket_id}")
        else:
            logging.warning(f"Ticket {ticket_id} not found")

async def get_user_tickets(user_id: int) -> list[Ticket]:
    async with async_session() as session:
        result = await session.execute(
            select(Ticket)
            .where(Ticket.telegram_id == user_id)
            .where(Ticket.closed_by_user == False)  # Фильтруем тикеты, которые пользователь еще не закрыл
        )
        tickets = result.scalars().all()
        return tickets
async def get_closed_tickets() -> list[Ticket]:
    async with async_session() as session:
        result = await session.execute(select(Ticket).where(Ticket.active == False))
        tickets = result.scalars().all()
        logging.info(f"Retrieved closed tickets: {tickets}")
        return tickets

# Эта функция для добавления вопроса из общего чата
async def add_question(user_id: int, question_text: str, subject: str, media: list = None, from_user: types.User = None):
    async with async_session() as session:
        # Проверяем, существует ли пользователь в базе данных
        result = await session.execute(select(User).where(User.telegram_id == user_id))
        user = result.scalars().first()

        # Если пользователь не найден, добавляем его
        if not user:
            # Извлекаем данные пользователя из from_user (если переданы)
            username = from_user.username if from_user and from_user.username else "unknown_user"
            full_name = f"{from_user.first_name or ''} {from_user.last_name or ''}".strip() if from_user else "Неизвестно"

            # Создаем запись для нового пользователя
            new_user = User(
                telegram_id=user_id,
                username=username,
                full_name=full_name,
                is_admin=False  # Это обычный пользователь
            )
            session.add(new_user)
            await session.commit()
            logging.info(f"Добавлен новый пользователь с telegram_id {user_id}.")

        # Если пользователь найден, проверяем и обновляем его данные
        else:
            updated = False
            if from_user:
                if user.username != from_user.username:
                    user.username = from_user.username
                    updated = True
                if user.full_name != f"{from_user.first_name or ''} {from_user.last_name or ''}".strip():
                    user.full_name = f"{from_user.first_name or ''} {from_user.last_name or ''}".strip()
                    updated = True
            if updated:
                await session.commit()
                logging.info(f"Данные пользователя с telegram_id {user_id} обновлены.")

        # Создание нового тикета
        ticket = Ticket(telegram_id=user_id, creation_time=datetime.utcnow(), last_updated=datetime.utcnow())
        session.add(ticket)
        await session.commit()

        # Создание нового вопроса
        new_question = Question(telegram_id=user_id, ticket_id=ticket.ticket_id, text=question_text, subject=subject)
        session.add(new_question)
        await session.commit()

        # Обновление времени последнего изменения тикета
        ticket.last_updated = datetime.utcnow()

        # Работа с медиафайлами, если они есть
        if media:
            for media_file in media:
                # Получаем содержимое файла и имя
                file_content = media_file.get('file')  # Это уже объект BytesIO
                filename = media_file.get('filename')

                # Асинхронная загрузка файла в S3
                file_url = await upload_to_s3(file_content, "fdfd", filename)

                # Добавление записи о медиафайле в базу данных
                file_type = 'image' if media_file.get('is_image') else 'video'
                media_entry = MediaFile(
                    file_url=file_url,
                    file_type=file_type,
                    filename=filename,
                    question_id=new_question.question_id,
                    ticket_id=ticket.ticket_id
                )
                session.add(media_entry)

        await session.commit()

        logging.info(f"Добавлен вопрос с тикетом {ticket.ticket_id}.")
        return new_question

# Эта функция для добавления вопроса (ответа) от пользователя на уже существующий тикет
async def add_question_to_ticket(user_id: int, ticket_id: int, question_text: str, subject: str, media_files: list = None):
    async with async_session() as session:
        # Проверка существования тикета
        result = await session.execute(select(Ticket).where(Ticket.ticket_id == ticket_id))
        ticket = result.scalars().first()

        if not ticket:
            raise ValueError(f"Тикет с id {ticket_id} не найден.")

        # Создание нового вопроса для существующего тикета
        new_question = Question(
            telegram_id=user_id,
            ticket_id=ticket_id,
            text=question_text,
            subject=subject,
            creation_time=datetime.utcnow()
        )
        session.add(new_question)

        # Работа с медиафайлами, если они есть
        if media_files:
            for media in media_files:
                # Считаем, что у нас есть логика для загрузки файлов на S3 или другое хранилище
                file_content = media.get('file')  # Это уже объект BytesIO
                filename = media.get('filename')
                file_url = await upload_to_s3(file_content, "fdfd", filename)

                # Добавляем запись о медиафайле в базу данных
                file_type = 'image' if media.get('is_image') else 'video'
                media_entry = MediaFile(
                    file_url=file_url,
                    file_type=file_type,
                    filename=filename,
                    question_id=new_question.question_id,  # Связываем с вопросом
                    ticket_id=new_question.ticket_id
                )
                session.add(media_entry)

        # Обновляем тикет (например, активируем его снова)
        ticket.active = True
        ticket.last_updated = datetime.utcnow()

        await session.commit()

        logging.info(f"Добавлен новый вопрос для тикета {ticket_id}.")
        return new_question


async def add_answer(admin_id: int, ticket_id: int, answer_text: str, media: list = None, from_user: types.User = None):
    async with async_session() as session:
        # Проверяем, существует ли пользователь в базе данных
        result = await session.execute(select(User).where(User.telegram_id == admin_id))
        user = result.scalars().first()

        if not user:
            # Извлекаем данные пользователя из `from_user`
            username = from_user.username if from_user and from_user.username else "unknown_user"
            full_name = f"{from_user.first_name or ''} {from_user.last_name or ''}".strip() if from_user else "Неизвестно"

            new_user = User(
                telegram_id=admin_id,
                username=username,
                full_name=full_name,
                is_admin=True  # Указываем, что это администратор
            )
            session.add(new_user)
            await session.commit()
            logging.info(f"Добавлен новый администратор с telegram_id {admin_id}.")
        else:
            # Обновляем данные пользователя, если необходимо
            username = from_user.username if from_user and from_user.username else user.username
            full_name = f"{from_user.first_name or ''} {from_user.last_name or ''}".strip() if from_user else user.full_name

            user.username = username
            user.full_name = full_name
            session.add(user)
            await session.commit()

        # Создаём новый ответ
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
    async with async_session() as session:
        result = await session.execute(
            select(Ticket).where(Ticket.telegram_id == user_id, Ticket.closed_by_user == True)
        )
        tickets = result.scalars().all()
        logging.info(f"Retrieved closed tickets for user {user_id}: {tickets}")
        return tickets


# End of code from combined_code.py

# Code from main.py
import os
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
from chains.rag_service import app as rag_app  # Импортируем ваше приложение FastAPI
import uvicorn

# Настройка логирования в консоль
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()]  # Добавляем StreamHandler для вывода логов в консоль
)
logger = logging.getLogger(__name__)

# Инициализация бота и диспетчера
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Включение маршрутов (handlers)
dp.include_router(auth_router)
dp.include_router(admin_router)
dp.include_router(user_router)
dp.include_router(active_ticket_router)
dp.include_router(closed_ticket_router)
dp.include_router(chat_router)

# Инициализация FastAPI
api_app = FastAPI()

# Включаем маршруты из rag_service в основное приложение FastAPI
api_app.mount("/rag", rag_app)  # Монтируем под `/rag`, чтобы сохранить структуру URL

async def on_startup(dispatcher: Dispatcher):
    logger.info("Применение миграций...")
    await init_db()  # Инициализация базы данных

    bot_info = await bot.get_me()  # Получаем информацию о боте
    dispatcher['bot_username'] = bot_info.username  # Сохраняем username бота в dispatcher
    logger.info(f"Имя пользователя бота: {dispatcher['bot_username']}")
    logger.info("Бот успешно запущен.")

async def start_fastapi_server():
    # Запуск FastAPI сервера на порту 8000
    config = uvicorn.Config(api_app, host="0.0.0.0", port=8000, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()

async def main():
    logger.info("Запуск бота...")
    await apply_migrations()  # Применение миграций перед запуском бота

    # Параллельный запуск бота и FastAPI
    await asyncio.gather(
        dp.start_polling(bot, on_startup=on_startup),
        start_fastapi_server()
    )

if __name__ == '__main__':
    # Запуск асинхронного события main()
    try:
        asyncio.run(main())
    except Exception as e:
        logger.critical(f"Критическая ошибка при запуске бота: {e}")
# End of code from main.py

# Code from states.py
from aiogram.fsm.state import State, StatesGroup


class QuestionStates(StatesGroup):
    WAITING_FOR_SUBJECT = State()
    WAITING_FOR_QUESTION = State()

class UserStates(StatesGroup):
    AUTHENTICATED_USER = State()
    WAITING_FOR_RESPONSE = State()
    VIEW_TICKET = State()

class AdminStates(StatesGroup):
    AUTHENTICATED_ADMIN = State()
    WAITING_FOR_RESPONSE = State()
    VIEW_TICKET = State()

class UserTicketStates(StatesGroup):
    WAITING_FOR_RESPONSE = State()
    VIEW_TICKET = State()
    WAITING_FOR_ADDITIONAL_RESPONSE = State()

# End of code from states.py

# Code from keyboards.py
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

def get_admin_keyboard():
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="/getusers 📋 Получить список пользователей")],
            #[KeyboardButton(text="/adduser ➕ Добавить нового пользователя")],
            [KeyboardButton(text="/getticket 📂 Показать активные тикеты")],
            [KeyboardButton(text="/getclosedticket 📂 Показать закрытые тикеты")],
            [KeyboardButton(text="/home 🏠 Вернуться в меню администратора")]
        ],
        resize_keyboard=True
    )
    return keyboard

def get_user_keyboard():
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="/showtickets 📂 Показать мои тикеты")],
            [KeyboardButton(text="/showclosedtickets 📂 Показать закрытые тикеты")]
        ],
        resize_keyboard=True
    )
    return keyboard
# End of code from keyboards.py

# Code from s3_utils.py
import boto3
import logging
import aiohttp
import io
from PIL import Image
from aiogram import Bot
from aiogram.types import BufferedInputFile
from aiogram.client.session import aiohttp
from botocore.exceptions import NoCredentialsError
from config import AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, S3_ENDPOINT_URL, S3_BUCKET_NAME


MAX_IMAGE_SIZE_MB = 3
MAX_VIDEO_SIZE_MB = 10
ALLOWED_IMAGE_FORMATS = ['jpg', 'JPEG', 'png']
ALLOWED_VIDEO_FORMATS = ['mp4', 'mov', 'm4v']  # Формат записи экрана macOS


# Инициализация клиента S3 с указанием хранилища Яндекса
s3 = boto3.client(
    's3',
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    endpoint_url=S3_ENDPOINT_URL  # Указываем URL хранилища Яндекса
)

async def upload_to_s3(file_obj, bucket_name, filename):
    try:
        s3.upload_fileobj(file_obj, bucket_name, filename)
        file_url = f"{S3_ENDPOINT_URL}/{bucket_name}/{filename}"
        return file_url
    except NoCredentialsError:
        logging.error("Ошибка доступа к Яндекс S3. Проверьте ключи доступа.")
        return None

async def validate_and_compress_media(media_files, message):
    valid_media = []

    for media_file in media_files:
        file_content = media_file.get('file')
        filename = media_file.get('filename')

        try:
            # Открываем файл как изображение для проверки
            image = Image.open(io.BytesIO(file_content.getvalue()))
            image.verify()  # Проверяем, что файл является изображением
            image = Image.open(io.BytesIO(file_content.getvalue()))  # Снова открываем для манипуляций (thumbnail)
            image_size_mb = len(file_content.getvalue()) / (1024 * 1024)

            if image_size_mb > MAX_IMAGE_SIZE_MB:
                logging.info(f"Сжатие изображения {filename}, размер: {image_size_mb} МБ")
                image.thumbnail((image.width // 2, image.height // 2))  # Сжимаем изображение
                buffer = io.BytesIO()
                image.save(buffer, format=image.format)
                file_content = buffer
                image_size_mb = len(buffer.getvalue()) / (1024 * 1024)
                logging.info(f"Новое изображение {filename}, размер: {image_size_mb} МБ")

            valid_media.append({
                'file': file_content,
                'filename': filename,
                'is_image': True
            })

        except (IOError, SyntaxError) as e:
            # Если файл не является изображением или поврежден
            logging.warning(f"Файл {filename} не поддерживается или поврежден: {e}")
            await message.reply(f"Файл {filename} не поддерживается или поврежден. Пожалуйста, отправьте изображение формата JPG, PNG.")
            continue

    return valid_media

async def send_file_from_url(bot: Bot, chat_id: int, file_url: str):
    try:
        # Игнорирование SSL-сертификатов
        async with aiohttp.ClientSession() as session:
            async with session.get(file_url, ssl=False) as response:
                if response.status == 200:
                    file_bytes = await response.read()
                    filename = file_url.split("/")[-1]

                    # Создаем BufferedInputFile с использованием загруженных байтов
                    input_file = BufferedInputFile(file_bytes, filename=filename)

                    # Отправляем файл как фото
                    await bot.send_photo(chat_id=chat_id, photo=input_file)
                else:
                    logging.error(f"Ошибка при загрузке файла {file_url}: {response.status}")
    except Exception as e:
        logging.error(f"Ошибка при отправке файла {file_url}: {e}")





# End of code from s3_utils.py

# Code from db_test.py
'''В этом файле копия файла db с оптимизированными запросами'''

import logging
import os
from datetime import datetime

from aiogram import types
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker, selectinload
from models import Base, User, Ticket, Question, Answer, Migration
from sqlalchemy.future import select
from sqlalchemy.sql import text
from config import DATABASE_URL

engine = create_async_engine(DATABASE_URL, echo=True)
async_session = sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False
)

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logging.info("Database initialized successfully.")

async def check_tables_exist() -> bool:
    async with async_session() as session:
        async with session.begin():
            result = await session.execute(
                text("SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'migrations');")
            )
            return result.scalar()

async def apply_migrations():
    migrations_folder = 'migrations'

    tables_exist = await check_tables_exist()

    async with async_session() as session:
        if tables_exist:
            result = await session.execute(select(Migration.migration_name))
            applied_migrations = set(row[0] for row in result.all())
        else:
            applied_migrations = set()
            logging.info("Таблицы не найдены в базе данных. Применение последней миграции.")

        all_migrations = [f for f in os.listdir(migrations_folder) if f.endswith('.sql')]
        new_migrations = [m for m in all_migrations if m not in applied_migrations]
        new_migrations.sort()

        if new_migrations:
            logging.info(f"Найдено {len(new_migrations)} новых миграций: {new_migrations}")

            async with engine.connect() as conn:
                async with conn.begin():
                    try:
                        for migration in new_migrations:
                            migration_file_path = os.path.join(migrations_folder, migration)
                            with open(migration_file_path, 'r', encoding='utf-8') as migration_file:
                                sql_commands = migration_file.read()

                            for command in sql_commands.split(';'):
                                command = command.strip()
                                if command:
                                    logging.info(f"Применение SQL команды:\n{command}")
                                    await conn.execute(text(command))

                        await conn.commit()

                        async with engine.connect() as inner_conn:
                            async with inner_conn.begin():
                                for migration in new_migrations:
                                    new_migration = Migration(migration_name=migration)
                                    session.add(new_migration)
                                await session.commit()
                                logging.info(f"Миграции {new_migrations} успешно применены.")
                    except Exception as e:
                        logging.error(f"Ошибка при применении миграции: {e}")
                        await conn.rollback()
                    else:
                        await conn.commit()
        else:
            logging.info("Новые миграции отсутствуют.")

async def get_user_by_telegram_id(telegram_id: int) -> User:
    async with async_session() as session:
        result = await session.execute(select(User).where(User.telegram_id == telegram_id).options(selectinload(User.tickets)))
        user = result.scalars().first()
        logging.info(f"Retrieved user by telegram ID: {user}")
        return user

async def get_active_tickets(offset: int = 0, limit: int = 10) -> list[Ticket]:
    async with async_session() as session:
        result = await session.execute(
            select(Ticket)
            .where(Ticket.active == True)
            .options(selectinload(Ticket.questions), selectinload(Ticket.answers))  # Жадная загрузка вопросов и ответов
            .order_by(Ticket.last_updated.desc())
            .offset(offset)
            .limit(limit)
        )
        tickets = result.scalars().all()

        # Не требуется выполнять отдельные запросы для каждого тикета, данные уже загружены
        for ticket in tickets:
            if ticket.answers:
                last_answer = max(ticket.answers, key=lambda a: a.answer_time)  # Получаем последний ответ
                ticket.last_admin_name = last_answer.user.username if last_answer.user else "Админ"
            else:
                ticket.last_admin_name = "Админ"

        logging.info(f"Retrieved active tickets: {tickets}")
        return tickets

async def get_questions_by_ticket_id(ticket_id: int) -> list[Question]:
    async with async_session() as session:
        result = await session.execute(
            select(Question)
            .where(Question.ticket_id == ticket_id)
            .options(selectinload(Question.ticket))
        )
        questions = result.scalars().all()
        logging.info(f"Retrieved questions for ticket {ticket_id}: {questions}")
        return questions

async def get_ticket_history(ticket_id: int) -> list:
    async with async_session() as session:
        questions = await session.execute(
            select(Question)
            .where(Question.ticket_id == ticket_id)
            .options(selectinload(Question.user))
        )
        answers = await session.execute(
            select(Answer)
            .where(Answer.ticket_id == ticket_id)
            .options(selectinload(Answer.user))
        )

        questions = questions.scalars().all()
        answers = answers.scalars().all()

        history = sorted(questions + answers, key=lambda x: x.creation_time)
        logging.info(f"Retrieved history for ticket {ticket_id}: {history}")
        return history

async def close_ticket(ticket_id: int):
    async with async_session() as session:
        result = await session.execute(
            select(Ticket)
            .where(Ticket.ticket_id == ticket_id)
            .options(selectinload(Ticket.questions), selectinload(Ticket.answers))
        )
        ticket = result.scalars().first()
        if ticket:
            ticket.active = False
            await session.commit()
            logging.info(f"Closed ticket {ticket_id}")
        else:
            logging.warning(f"Ticket {ticket_id} not found")

async def close_ticket_by_admin(ticket_id: int):
    async with async_session() as session:
        result = await session.execute(
            select(Ticket)
            .where(Ticket.ticket_id == ticket_id)
            .options(selectinload(Ticket.questions), selectinload(Ticket.answers))
        )
        ticket = result.scalars().first()
        if ticket:
            ticket.active = False
            await session.commit()
            logging.info(f"Admin closed ticket {ticket_id}")
        else:
            logging.warning(f"Ticket {ticket_id} not found")

async def close_ticket_by_user(ticket_id: int):
    async with async_session() as session:
        result = await session.execute(
            select(Ticket)
            .where(Ticket.ticket_id == ticket_id)
            .options(selectinload(Ticket.questions), selectinload(Ticket.answers))
        )
        ticket = result.scalars().first()
        if ticket:
            ticket.active = not ticket.active
            await session.commit()
            logging.info(f"User toggled ticket {ticket_id} status")
        else:
            logging.warning(f"Ticket {ticket_id} not found")

async def get_user_tickets(user_id: int) -> list[Ticket]:
    async with async_session() as session:
        result = await session.execute(
            select(Ticket)
            .where(Ticket.telegram_id == user_id, Ticket.closed_by_user == False)
            .options(selectinload(Ticket.questions), selectinload(Ticket.answers))  # Жадная загрузка вопросов и ответов
        )
        tickets = result.scalars().all()
        return tickets

async def get_closed_tickets() -> list[Ticket]:
    async with async_session() as session:
        result = await session.execute(
            select(Ticket)
            .where(Ticket.active == False)
            .options(selectinload(Ticket.questions), selectinload(Ticket.answers))
        )
        tickets = result.scalars().all()
        logging.info(f"Retrieved closed tickets: {tickets}")
        return tickets

async def add_question(user_id: int, question_text: str, subject: str):
    async with async_session() as session:
        result = await session.execute(select(User).where(User.telegram_id == user_id))
        user = result.scalars().first()

        if not user:
            new_user = User(
                telegram_id=user_id,
                username="unknown_user",
                full_name="Неизвестно"
            )
            session.add(new_user)
            await session.commit()
            logging.info(f"Добавлен новый пользователь с telegram_id {user_id}.")

        ticket = Ticket(
            telegram_id=user_id,
            creation_time=datetime.utcnow(),
            last_updated=datetime.utcnow()
        )
        session.add(ticket)
        await session.commit()

        new_question = Question(
            telegram_id=user_id,
            ticket_id=ticket.ticket_id,
            text=question_text,
            subject=subject
        )
        session.add(new_question)
        ticket.last_updated = datetime.utcnow()

        await session.commit()

        logging.info(f"Добавлен вопрос в тикет {ticket.ticket_id}.")
        return new_question

async def add_question_to_ticket(user_id: int, ticket_id: int, question_text: str, subject: str = None):
    async with async_session() as session:
        new_question = Question(
            telegram_id=user_id,
            ticket_id=ticket_id,
            text=question_text,
            subject=subject
        )
        session.add(new_question)
        await session.commit()

        logging.info(f"Добавлен вопрос (ответ пользователя) в тикет {ticket_id}.")
        return new_question

async def add_answer(admin_id: int, ticket_id: int, answer_text: str):
    async with async_session() as session:
        new_answer = Answer(
            ticket_id=ticket_id,
            telegram_id=admin_id,
            text=answer_text
        )
        session.add(new_answer)

        result = await session.execute(select(Ticket).where(Ticket.ticket_id == ticket_id))
        ticket = result.scalars().first()
        if ticket:
            ticket.last_updated = datetime.utcnow()
            await session.commit()
            logging.info(f"Добавлен ответ администратора в тикет {ticket_id}.")
            return new_answer, ticket
        else:
            logging.warning(f"Тикет {ticket_id} не найден.")
            return None, None

async def get_user_closed_tickets(user_id: int) -> list[Ticket]:
    async with async_session() as session:
        result = await session.execute(
            select(Ticket)
            .where(Ticket.telegram_id == user_id, Ticket.closed_by_user == True)
            .options(selectinload(Ticket.questions), selectinload(Ticket.answers))
        )
        tickets = result.scalars().all()
        logging.info(f"Retrieved closed tickets for user {user_id}: {tickets}")
        return tickets

# End of code from db_test.py

# Code from rag_service.py
import logging
from http.client import HTTPException

from langchain.chains import LLMChain
from langchain.chains.combine_documents.stuff import StuffDocumentsChain
from langchain_core.prompts.prompt import PromptTemplate
from langchain_community.llms import YandexGPT
from langchain_community.embeddings.yandex import YandexGPTEmbeddings
from langchain.text_splitter import RecursiveCharacterTextSplitter
from pydantic import BaseModel
from fastapi import FastAPI

from chains.chroma_utils import initialize_chroma_client, add_documents_to_chroma, search_similar_docs

app = FastAPI()

class Query(BaseModel):
    text: str

def load_text_file(file_path: str) -> str:
    """Загрузка текста из файла.

    Args:
        file_path (str): Путь к текстовому файлу.

    Returns:
        str: Содержимое файла.
    """
    with open(file_path, 'r', encoding='utf-8') as file:
        data = file.read()
    return data

def split_text_into_chunks(text: str, chunk_size: int = 300, chunk_overlap: int = 30) -> list:
    """Разбивает текст на чанки с помощью RecursiveCharacterTextSplitter.

    Args:
        text (str): Исходный текст.
        chunk_size (int, optional): Размер чанка в словах. По умолчанию 300.
        chunk_overlap (int, optional): Перекрытие между чанками. По умолчанию 50.

    Returns:
        list: Список чанков текста.
    """
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap
    )
    chunks = splitter.split_text(text)
    return [{"id": f"chunk_{i+1}", "text": chunk} for i, chunk in enumerate(chunks)]

def get_yagpt_embeddings(folder_id: str, token: str) -> YandexGPTEmbeddings:
    """Получение эмбеддингов Yandex GPT.

    Args:
        folder_id (str): ID папки Yandex GPT.
        token (str): IAM токен Yandex GPT.

    Returns:
        YandexGPTEmbeddings: Эмбеддинги Yandex GPT.
    """
    embeddings = YandexGPTEmbeddings(iam_token=token, folder_id=folder_id)
    return embeddings


from fastapi import HTTPException


@app.post("/embeddings")
def load_embeddings(token: str, folder_id: str, txt_path: str):
    """Загрузка эмбеддингов в Chroma.

    Args:
        token (str): IAM токен Yandex GPT.
        folder_id (str): ID папки Yandex GPT.
        txt_path (str): Путь к текстовому файлу.
    """
    # Логирование входящих параметров
    logging.info(
        f"Получен запрос на /embeddings с параметрами: token={token}, folder_id={folder_id}, txt_path={txt_path}")

    try:
        # Загрузка текста и разбивка на чанки
        text = load_text_file(txt_path)
        logging.info(f"Текст успешно загружен из {txt_path}, длина текста: {len(text)} символов.")

        chunks = split_text_into_chunks(text)
        logging.info(f"Текст разбит на {len(chunks)} чанков.")

        embeddings = get_yagpt_embeddings(folder_id=folder_id, token=token)
        logging.info("Эмбеддинги успешно получены.")

        # Инициализация клиента и коллекции Chroma
        knowledge_base = initialize_chroma_client(collection_name="knowledge_base")
        logging.info("Клиент Chroma успешно инициализирован.")

        # Добавление документов в Chroma
        add_documents_to_chroma(knowledge_base, chunks, embeddings)
        logging.info("Документы успешно добавлены в Chroma.")
    except Exception as e:
        logging.error(f"Ошибка при выполнении загрузки эмбеддингов: {e}")
        raise HTTPException(status_code=500, detail="Ошибка при загрузке эмбеддингов.")

@app.get("/similar_docs")
def search_docs(query: Query, k: int = 2):
    """Поиск документов, похожих на запрос.

    Args:
        query (Query): Запрос
        k (int, optional): Количество результатов. По умолчанию 2.

    Returns:
        list: Список похожих документов
    """
    knowledge_base = initialize_chroma_client(collection_name="knowledge_base")
    docs = search_similar_docs(knowledge_base, query.text, k)
    return docs

@app.post("/llm_rag")
def query_llm_rag(token: str, folder_id: str, query: Query):
    """Запрос к Yandex GPT с использованием RAG.

    Args:
        token (str): IAM токен Yandex GPT.
        folder_id (str): ID папки Yandex GPT.
        query (Query): Запрос.

    Returns:
        str: Ответ.
    """
    # Промпт для обработки документов
    document_prompt = PromptTemplate(
        input_variables=["page_content"],
        template="{page_content}"
    )

    # Промпт для языковой модели
    document_variable_name = "context"
    stuff_prompt_override = """
        Прими во внимание приложенные к вопросу тексты и дай ответ на вопрос.
        Текст:
        -----
        {context}
        -----
        Вопрос:
        {query}
    """
    prompt = PromptTemplate(
        template=stuff_prompt_override,
        input_variables=["context", "query"]
    )

    llm = YandexGPT(iam_token=token, folder_id=folder_id)

    # Создание цепочки
    llm_chain = LLMChain(llm=llm, prompt=prompt)
    chain = StuffDocumentsChain(
        llm_chain=llm_chain,
        document_prompt=document_prompt,
        document_variable_name=document_variable_name,
    )

    # Поиск похожих документов в Chroma
    knowledge_base = initialize_chroma_client(collection_name="knowledge_base")
    docs = search_similar_docs(knowledge_base, query.text)

    # Генерация ответа на основе найденных документов и запроса
    response = chain.invoke({'query': query.text, 'input_documents': docs})

    return response
# End of code from rag_service.py

# Code from __init__.py

# End of code from __init__.py

# Code from chroma_utils.py
from chromadb import Client

from chromadb import Client


def initialize_chroma_client(collection_name: str, persist_directory: str = "/Users/nickstanchenkov/FD_bot_v3/FD_bot_v3/utils") -> Client:
    """
    Инициализация клиента Chroma и подключение к коллекции с поддержкой сохранения данных.

    Args:
        collection_name (str): Имя коллекции.
        persist_directory (str): Директория для хранения данных Chroma.

    Returns:
        Client: Клиент Chroma и подключенная коллекция.
    """
    # Создание клиента для работы с Chroma с указанием папки для сохранения данных
    chroma_client = Client(persist_directory=persist_directory, persist=True)

    # Проверка существования коллекции и её создание при необходимости
    if collection_name not in chroma_client.list_collections():
        chroma_client.create_collection(
            name=collection_name,
            metadata={
                "description": "Коллекция для хранения эмбеддингов и текстов из базы знаний"
            }
        )

    # Возвращаем подключение к коллекции
    return chroma_client.get_collection(name=collection_name)


def add_documents_to_chroma(knowledge_base, chunks, embeddings):
    """
    Векторизация текста и сохранение в Chroma.

    Args:
        knowledge_base: Коллекция Chroma.
        chunks (list): Список чанков для добавления.
        embeddings: Эмбеддинги Yandex GPT для векторизации текста.
    """
    for chunk in chunks:
        text = chunk["text"]
        embedding = embeddings.embed_text(text)
        knowledge_base.add_documents(
            documents=[{
                "id": chunk["id"],
                "text": text,
                "metadata": chunk["metadata"]
            }],
            embeddings=[embedding]
        )


def search_similar_docs(knowledge_base, query_text, k=2):
    """
    Поиск похожих документов в Chroma.

    Args:
        knowledge_base: Коллекция Chroma.
        query_text (str): Текст запроса.
        k (int, optional): Количество результатов. По умолчанию 2.

    Returns:
        list: Похожие документы.
    """
    return knowledge_base.similarity_search(query_text, k)
# End of code from chroma_utils.py

# Code from active_ticket_handlers.py
import logging
from datetime import datetime, timedelta
from aiogram import types, Router, Bot
from aiogram.fsm.context import FSMContext
from sqlalchemy import select

from states import AdminStates
from db import get_active_tickets, get_ticket_history, close_ticket_by_admin, async_session, add_answer, add_question
from models import Answer, Question, User, MediaFile
from aiogram.filters import Command, StateFilter
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

from utils.s3_utils import send_file_from_url, validate_and_compress_media

router = Router()

@router.message(Command(commands=['getticket']), StateFilter(AdminStates.AUTHENTICATED_ADMIN))
async def get_tickets_handler(message: types.Message, state: FSMContext):
    await show_tickets_page(message, state, page=0)

async def show_tickets_page(message: types.Message, state: FSMContext, page: int):
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
                button_text = f"Тикет {ticket.ticket_id}: {subject} (ответил: {ticket.last_admin_name}) {emoji}"
                keyboard.inline_keyboard.append([InlineKeyboardButton(text=button_text, callback_data=f"view_active_ticket_{ticket.ticket_id}")])

        if page > 0:
            keyboard.inline_keyboard.append([InlineKeyboardButton(text="⬅️ Предыдущая", callback_data=f"tickets_page_{page - 1}")])
        if len(tickets) == tickets_per_page:
            keyboard.inline_keyboard.append([InlineKeyboardButton(text="➡️ Следующая", callback_data=f"tickets_page_{page + 1}")])

        keyboard.inline_keyboard.append([InlineKeyboardButton(text="🏠 Вернуться", callback_data="return_to_authorized")])

        await message.answer("📂 Активные тикеты:", reply_markup=keyboard)
        logging.info(f"Администратор {message.from_user.id} запросил активные тикеты. Страница: {page}")
        await state.update_data(viewing_closed_tickets=False, current_page=page)
        await state.set_state(AdminStates.AUTHENTICATED_ADMIN)
    except Exception as e:
        logging.error(f"Ошибка при запросе активных тикетов администратором {message.from_user.id}: {e}")
        await message.answer("❌ Произошла ошибка при обработке вашего запроса. Попробуйте позже.")

@router.callback_query(lambda c: c.data.startswith('view_active_ticket_'), StateFilter(AdminStates.AUTHENTICATED_ADMIN))
async def view_active_ticket(callback_query: CallbackQuery, state: FSMContext):
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

        # Проверка наличия медиафайлов как в вопросах, так и в ответах
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
                [InlineKeyboardButton(text="✏️ Ответить", callback_data=f"answer_ticket_{ticket_id}")],
                [InlineKeyboardButton(text="🔒 Закрыть Тикет", callback_data=f"close_ticket_{ticket_id}")],
                [InlineKeyboardButton(text="🔙 Вернуться", callback_data="get_active_tickets")]
            ]
        )

        # Добавляем кнопку для скачивания медиа, если файлы есть
        if has_media_files:
            keyboard.inline_keyboard.insert(2, [InlineKeyboardButton(text="📥 Скачать медиа", callback_data=f"download_media_{ticket_id}")])

        await callback_query.message.answer(text, parse_mode="HTML", reply_markup=keyboard)
        logging.info(f"Показан тикет {ticket_id} администратору {callback_query.from_user.id}.")
        await state.update_data(ticket_id=ticket_id, ticket_text=text)
        await state.set_state(AdminStates.VIEW_TICKET)
    except Exception as e:
        logging.error(f"Ошибка при просмотре тикета {ticket_id} администратором {callback_query.from_user.id}: {e}")
        await callback_query.message.edit_text("❌ Произошла ошибка при обработке вашего запроса. Попробуйте позже.")

@router.callback_query(lambda c: c.data.startswith('answer_ticket_'), StateFilter(AdminStates.VIEW_TICKET))
async def answer_ticket(callback_query: CallbackQuery, state: FSMContext):
    try:
        data = await state.get_data()
        ticket_text = data['ticket_text']
        await callback_query.message.edit_text(f"{ticket_text}\n\n✏️ Пожалуйста, введите ваш ответ.")
        await state.set_state(AdminStates.WAITING_FOR_RESPONSE)
        state_data = await state.get_state()
        print(state_data)
    except Exception as e:
        logging.error(f"Ошибка при подготовке к ответу на тикет {callback_query.data.split('_')[3]} администратором {callback_query.from_user.id}: {e}")
        await callback_query.message.edit_text("❌ Произошла ошибка при обработке вашего запроса. Попробуйте позже.")

@router.message(StateFilter(AdminStates.WAITING_FOR_RESPONSE))
async def receive_answer(message: types.Message, state: FSMContext):
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
                [InlineKeyboardButton(text="📋 Вернуться к тикету", callback_data=f"view_active_ticket_{ticket.ticket_id}")],
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
    try:
        data = await state.get_data()
        page = data.get('current_page', 0)
        await show_tickets_page(callback_query.message, state, page)
    except Exception as e:
        logging.error(f"Ошибка при возврате к списку тикетов администратором {callback_query.from_user.id}: {e}")
        await callback_query.message.edit_text("❌ Произошла ошибка при обработке вашего запроса. Попробуйте позже.")

@router.callback_query(lambda c: c.data == 'get_active_tickets', StateFilter(AdminStates.VIEW_TICKET))
async def return_to_active_tickets(callback_query: CallbackQuery, state: FSMContext):
    logging.info(f"Возвращение к активным тикетам. Callback data: {callback_query.data}")
    try:
        data = await state.get_data()
        page = data.get('current_page', 0)
        await show_tickets_page(callback_query.message, state, page)
        logging.info(f"Возвращен список активных тикетов на странице {page}.")
    except Exception as e:
        logging.error(f"Ошибка при возврате к списку активных тикетов администратором {callback_query.from_user.id}: {e}")
        await callback_query.message.edit_text("❌ Произошла ошибка при обработке вашего запроса. Попробуйте позже.")

@router.callback_query(lambda c: c.data.startswith('close_ticket_'), StateFilter(AdminStates.VIEW_TICKET))
async def close_ticket_handler(callback_query: CallbackQuery, state: FSMContext):
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


# End of code from active_ticket_handlers.py

# Code from __init__.py

# End of code from __init__.py

# Code from auth_handlers.py
import logging
from aiogram import types, Bot, Router
from aiogram.fsm.context import FSMContext
from config import ADMIN_IDS
from states import AdminStates, UserStates
from aiogram.filters import Command, StateFilter
from db import get_user_by_telegram_id


from handlers.admin_handlers import get_users_handler, admin_home, load_embeddings_handler
from handlers.active_ticket_handlers import get_tickets_handler
from handlers.closed_ticket_handlers import get_closed_tickets_handler

from utils.keyboards import get_admin_keyboard, get_user_keyboard
from handlers.user_handlers import show_tickets_handler, \
    show_closed_tickets_handler  # Импортируем обработчик show_tickets_handler для пользователей

router = Router()

# Команды администратора
admin_commands = {
    "/getusers": get_users_handler,
    #"/adduser": add_user_start,
    "/home": admin_home,
    "/getticket": get_tickets_handler,
    "/getclosedticket": get_closed_tickets_handler,
    "/load_embeddings": load_embeddings_handler
}

# Команды пользователя
user_commands = {
    "/showtickets": show_tickets_handler,
    "/showclosedtickets": show_closed_tickets_handler
}

async def set_admin_commands(bot: Bot):
    admin_commands = [
        types.BotCommand(command="/getusers", description="📋 Получить список пользователей"),
        #types.BotCommand(command="/adduser", description="➕ Добавить нового пользователя"),
        types.BotCommand(command="/getticket", description="📂 Показать активные тикеты"),
        types.BotCommand(command="/getclosedticket", description="📂 Показать закрытые тикеты"),
        types.BotCommand(command="/home", description="🏠 Вернуться в меню администратора"),
        types.BotCommand(command="/load_embeddings", description="📥 Загрузить эмбеддинги в Chroma")
    ]
    await bot.set_my_commands(admin_commands)
    logging.info("Admin commands set.")

async def set_user_commands(bot: Bot):
    user_commands = [
        types.BotCommand(command="/showtickets", description="📂 Показать мои тикеты"),
        types.BotCommand(command="/showclosedtickets", description="📂 Показать закрытые тикеты")
    ]
    await bot.set_my_commands(user_commands)
    logging.info("User commands set.")

@router.message(Command(commands=['start']))
async def start_handler(message: types.Message, state: FSMContext):
    if message.chat.type != 'private':
        logging.info(f"Команда /start была вызвана в чате {message.chat.id}. Игнорирование.")
        return

    user_id = message.from_user.id

    # Проверка, является ли пользователь администратором
    if user_id in ADMIN_IDS:
        await state.set_state(AdminStates.AUTHENTICATED_ADMIN)
        await set_admin_commands(message.bot)
        await message.answer(
            f"✅ Вы успешно аутентифицированы как администратор, {message.from_user.first_name}.",
            reply_markup=get_admin_keyboard()
        )
        logging.info(f"Администратор {user_id} ({message.from_user.first_name}) успешно аутентифицирован.")
    else:
        # Проверка наличия пользователя в базе данных
        user = await get_user_by_telegram_id(user_id)
        if user:
            # Пользователь найден в базе данных
            await state.set_state(UserStates.AUTHENTICATED_USER)
            await set_user_commands(message.bot)
            await message.answer(
                "✅ Вы успешно аутентифицированы как пользователь. Используйте команды для взаимодействия с тикетами.",
                reply_markup=get_user_keyboard()
            )
            logging.info(f"Пользователь {user_id} успешно аутентифицирован.")
        else:
            # Пользователь не найден в базе данных
            await message.answer("❌ Доступ запрещен. Вы не зарегистрированы в системе.")
            logging.warning(f"Неизвестный пользователь {user_id} попытался выполнить команду /start.")

@router.message(StateFilter(AdminStates.AUTHENTICATED_ADMIN))
async def handle_private_message(message: types.Message, state: FSMContext):
    if message.chat.type == 'private':
        if message.text.startswith('/'):
            command = message.text.split()[0]
            handler = admin_commands.get(command)
            print(handler)
            if handler:
                logging.info(f"Администратор {message.from_user.id} вызвал команду {command}.")
                await handler(message, state)
            else:
                await message.answer(f"Команда не распознана.{command}")
        else:
            await message.answer("Пожалуйста, используйте одну из команд администратора.")
        logging.info(f"Администратор {message.from_user.id} отправил сообщение в личных сообщениях.")
    else:
        logging.info(f"Игнорирование сообщения из чата {message.chat.id}.")

@router.message(StateFilter(UserStates.AUTHENTICATED_USER))
async def handle_user_message(message: types.Message, state: FSMContext):
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

# End of code from auth_handlers.py

# Code from closed_ticket_handlers.py
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
    try:
        ticket_id = int(callback_query.data.split('_')[2])
        history = await get_ticket_history(ticket_id)

        if not history:
            await callback_query.message.edit_text("📝 Нет сообщений в этом тикете.")
            return

        async with async_session() as session:
            # Получаем первого пользователя, кто создал тикет
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
    try:
        await callback_query.message.edit_text("🏠 Вы вернулись в меню администратора.")
        await state.set_state(AdminStates.AUTHENTICATED_ADMIN)
        logging.info(f"Администратор {callback_query.from_user.id} вернулся в меню.")
    except Exception as e:
        logging.error(f"Ошибка при возврате в меню администратором {callback_query.from_user.id}: {e}")
        await callback_query.message.edit_text("❌ Произошла ошибка при обработке вашего запроса. Попробуйте позже.")

# End of code from closed_ticket_handlers.py

# Code from chat_handlers.py
import logging
import os

from aiogram import types, Router, Bot
from aiogram.fsm.context import FSMContext
from aiohttp import ClientSession

from config import RAG_API_URL, LLM_RAG_ENDPOINT
from db import add_question
from utils.s3_utils import validate_and_compress_media

router = Router()

@router.message()
async def handle_group_message(message: types.Message, state: FSMContext):
    bot_info = await message.bot.get_me()
    bot_username = bot_info.username

    # Запрет использования команд бота в группах
    if message.text and message.text.startswith('/'):
        await message.reply("Использование команд бота в групповых чатах запрещено.")
        return

    # Проверка на упоминание бота и обработка сообщения
    if message.text and f"@{bot_username}" in message.text:
        logging.info(f"Бот упомянут в группе {message.chat.id} пользователем {message.from_user.id}: {message.text}")
        await handle_mention(message, state)
    elif message.caption and f"@{bot_username}" in message.caption:
        logging.info(
            f"Бот упомянут в подписи к медиа сообщению в группе {message.chat.id} пользователем {message.from_user.id}: {message.caption}")
        await handle_mention(message, state)


async def handle_mention(message: types.Message, state: FSMContext):
    bot_info = await message.bot.get_me()
    bot_username = bot_info.username
    text = (message.text or message.caption).replace(f"@{bot_username}", "").strip()

    if not text:
        await message.reply("Вы не задали вопрос. Пожалуйста, введите ваш вопрос.")
        return

    # Отправляем запрос на RAG-сервис для обработки вопроса
    async with ClientSession() as session:
        payload = {
            "token": "ваш_iam_token",  # Реальные значения IAM токена и ID папки
            "folder_id": "ваш_folder_id",
            "query": {"text": text}
        }

        try:
            # Отправляем запрос на эндпоинт RAG для получения ответа
            async with session.post(f"{RAG_API_URL}{LLM_RAG_ENDPOINT}", json=payload) as response:
                if response.status == 200:
                    result = await response.json()
                    answer = result.get("response", "Извините, я не смог найти ответ на ваш вопрос.")
                    await message.reply(answer)
                else:
                    await message.reply("Ошибка при обработке запроса. Попробуйте позже.")
        except Exception as e:
            logging.error(f"Ошибка при взаимодействии с RAG сервисом: {e}")
            await message.reply(f"Произошла ошибка: {str(e)}")

def extract_subject(text: str) -> str:
    """ Извлекает тему из сообщения (по первому слову или предложению) """
    return text.split('.')[0] if '.' in text else text.split()[0]

async def notify_admins_about_question(bot: Bot, message: types.Message, subject: str):
    # Получаем username или имя пользователя
    user_display_name = message.from_user.username or message.from_user.full_name or "Пользователь без имени"

    # Уведомление для администраторов
    notification_message = f"Пользователь {user_display_name} задал вопрос с темой '{subject}'."

    # Получаем список администраторов из конфига
    admin_ids = [int(admin_id) for admin_id in os.getenv('ADMIN_IDS').split(',')]

    # Отправляем уведомление каждому админу
    for admin_id in admin_ids:
        try:
            await bot.send_message(admin_id, notification_message)
        except Exception as e:
            logging.error(f"Ошибка при отправке уведомления админу {admin_id}: {e}")




"""Кусок десли нужно будет добавить вплидацию на все документы"""
"""# Если есть документ, который не является фото или видео, отправляем предупреждение
    elif message.document:
        file_info = await message.bot.get_file(message.document.file_id)
        file_type = file_info.file_path.split('.')[-1].lower()

        if file_type not in ALLOWED_IMAGE_FORMATS and file_type not in ALLOWED_VIDEO_FORMATS:
            logging.warning(f"Файл {file_info.file_path} не является поддерживаемым медиафайлом.")
            await message.reply(f"Файл {file_info.file_path} не поддерживается. Пожалуйста, отправьте файл формата JPG, PNG, MP4 или MOV.")
            return"""
# End of code from chat_handlers.py

# Code from user_handlers.py
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
# End of code from user_handlers.py

# Code from admin_handlers.py
import logging
import aiohttp
import config
from aiogram import types, Router, Bot
from aiogram.fsm.context import FSMContext
from utils.keyboards import get_admin_keyboard
from states import AdminStates
from db import async_session
from sqlalchemy.future import select
from models import User
from aiogram.filters import Command, StateFilter

router = Router()

async def set_admin_commands(bot: Bot):
    admin_commands = [
        types.BotCommand(command="/getusers", description="📋 Получить список пользователей"),
        #types.BotCommand(command="/adduser", description="➕ Добавить нового пользователя"),
        types.BotCommand(command="/getticket", description="📂 Показать активные тикеты"),
        types.BotCommand(command="/getclosedticket", description="📂 Показать закрытые тикеты"),
        types.BotCommand(command="/home", description="🏠 Вернуться в меню администратора"),
        types.BotCommand(command="/loadembeddings", description="📥 Загрузить эмбеддинги в Chroma")
    ]
    await bot.set_my_commands(admin_commands)
    # await bot.delete_my_commands(scope=BotCommandScopeChat(chat_id=config.CHAT_ID))  # Замените GROUP_CHAT_ID на ID вашей группы
    logging.info("Admin commands set.")

@router.message(Command(commands=['getusers']), StateFilter(AdminStates.AUTHENTICATED_ADMIN))
async def get_users_handler(message: types.Message, state: FSMContext):
    try:
        async with async_session() as session:
            result = await session.execute(select(User))
            users = result.scalars().all()

            if not users:
                await message.answer("📋 Список пользователей пуст.")
                return

            user_list = "\n\n".join([
                f"👤 <b>Имя:</b> {user.username}\n"
                f"👥 <b>Фамилия:</b> {user.full_name}\n"
                f"🔧 <b>Роль:</b> {'Админ' if user.is_admin else 'Пользователь'}"
                for user in users
            ])
            await message.answer(f"📋 <b>Список пользователей:</b>\n\n{user_list}", parse_mode="HTML")
            logging.info(f"Администратор {message.from_user.id} запросил список пользователей.")
    except Exception as e:
        logging.error(f"Ошибка при запросе списка пользователей администратором {message.from_user.id}: {e}")
        await message.answer("❌ Произошла ошибка при обработке вашего запроса. Попробуйте позже.")
    finally:
        await set_admin_commands(message.bot)  # Установка команд для админа после выполнения команды


@router.message(Command(commands=['home']), StateFilter(AdminStates.AUTHENTICATED_ADMIN))
async def admin_home(message: types.Message, state: FSMContext):
    await message.answer("🏠 Вы вернулись в меню администратора.", reply_markup=get_admin_keyboard())
    await state.set_state(AdminStates.AUTHENTICATED_ADMIN)
    logging.info(f"Администратор {message.from_user.id} вернулся в меню.")
    await set_admin_commands(message.bot)  # Установка команд для админа после возврата в меню


@router.message(Command(commands=['load_embeddings']), StateFilter(AdminStates.AUTHENTICATED_ADMIN))
async def load_embeddings_handler(message: types.Message, state: FSMContext):
    # Настройки и данные для отправки запроса на /embeddings/
    token = config.IAM_TOKEN  # IAM токен из config
    folder_id = config.FOLDER_ID  # Идентификатор папки из config
    txt_path = config.TXT_PATH  # Путь к текстовому файлу из config
    embeddings_endpoint = f"{config.RAG_API_URL}/embeddings"

    # Логирование данных перед отправкой
    logging.info(f"Эндпоинт для эмбеддингов: {embeddings_endpoint}")
    logging.info(f"Параметры запроса: token={token}, folder_id={folder_id}, txt_path={txt_path}")

    payload = {
        "token": token,
        "folder_id": folder_id,
        "txt_path": txt_path  # Путь к текстовому файлу
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(embeddings_endpoint, json=payload) as response:
                logging.info(f"Ответ от сервера: статус {response.status}")
                if response.status == 200:
                    await message.answer("✅ Эмбеддинги успешно загружены в Chroma.")
                    logging.info(f"Эмбеддинги успешно загружены администратором {message.from_user.id}.")
                else:
                    response_text = await response.text()
                    await message.answer("❌ Ошибка при загрузке эмбеддингов.")
                    logging.error(
                        f"Ошибка при загрузке эмбеддингов администратором {message.from_user.id}: {response.status}, ответ: {response_text}")
    except Exception as e:
        logging.error(f"Ошибка при выполнении запроса на загрузку эмбеддингов: {e}")
        await message.answer("❌ Произошла ошибка при загрузке эмбеддингов. Попробуйте позже.")
    finally:
        await set_admin_commands(message.bot)


"""Ниже идет закоменченный код из первой версии
 на добавление пользователям с номером телефона"""
# @router.message(Command(commands=['adduser']), StateFilter(AuthStates.AUTHENTICATED_ADMIN))
# async def add_user_start(message: types.Message, state: FSMContext):
#     await message.answer("✏️ Введите имя нового пользователя:")
#     await state.set_state(AddUserStates.WAITING_FOR_FIRST_NAME

# @router.message(StateFilter(AddUserStates.WAITING_FOR_FIRST_NAME))
# async def add_user_first_name(message: types.Message, state: FSMContext):
#     await state.update_data(first_name=message.text.strip())
#     await message.answer("✏️ Введите фамилию нового пользователя:")
#     await state.set_state(AddUserStates.WAITING_FOR_LAST_NAME)
#
# @router.message(StateFilter(AddUserStates.WAITING_FOR_LAST_NAME))
# async def add_user_last_name(message: types.Message, state: FSMContext):
#     await state.update_data(last_name=message.text.strip())
#     await message.answer("📞 Введите номер телефона нового пользователя (должен начинаться с '8' и содержать 11 символов):")
#     await state.set_state(AddUserStates.WAITING_FOR_PHONE)
#
# @router.message(StateFilter(AddUserStates.WAITING_FOR_PHONE))
# async def add_user_phone(message: types.Message, state: FSMContext):
#     phone_number = message.text.strip()
#     if len(phone_number) != 11 or not phone_number.startswith('8'):
#         await message.answer("📞 Номер телефона должен содержать 11 символов и начинаться с '8'. Пожалуйста, попробуйте снова.")
#         return
#
#     await state.update_data(phone=phone_number)
#     keyboard = InlineKeyboardMarkup(inline_keyboard=[
#         [InlineKeyboardButton(text="🔧 Администратор", callback_data="role_admin")],
#         [InlineKeyboardButton(text="👥 Пользователь", callback_data="role_user")]
#     ])
#     await message.answer("🔧 Выберите роль нового пользователя:", reply_markup=keyboard)
#     await state.set_state(AddUserStates.WAITING_FOR_ROLE)
#
# @router.callback_query(StateFilter(AddUserStates.WAITING_FOR_ROLE))
# async def add_user_role(callback_query: CallbackQuery, state: FSMContext):
#     role = callback_query.data.split('_')[1]
#     user_data = await state.get_data()
#     phone_hash = hashlib.sha256(user_data['phone'].encode()).hexdigest()
#     async with async_session() as session:
#         new_user = User(
#             first_name=user_data['first_name'],
#             second_name=user_data['last_name'],
#             phone_hash=phone_hash,
#             is_admin=(role == 'admin')
#         )
#         session.add(new_user)
#         await session.commit()
#
#     await callback_query.message.edit_text(f"✅ Новый пользователь {user_data['first_name']} {user_data['last_name']} успешно добавлен.", reply_markup=get_admin_keyboard())
#     await state.set_state(AuthStates.AUTHENTICATED_ADMIN)  # Возвращение в состояние аутентификации администратора
#     await set_admin_commands(callback_query.bot)  # Установка команд для админа после добавления пользователя


# End of code from admin_handlers.py
