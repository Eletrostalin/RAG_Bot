import io
import logging
import os
import tempfile

import aiohttp
from aiogram.enums import ContentType
from botocore.exceptions import NoCredentialsError

import config
from aiogram.types import Message, FSInputFile
from aiogram import types, Router, Bot, F
from aiogram.fsm.context import FSMContext
from chains.chroma_utils import (get_documents_from_chroma, initialize_chroma_client,
                                 clear_chroma_collection)
from utils.keyboards import get_admin_inline_keyboard
from utils.s3_utils import s3, upload_to_s3_db
from states import AdminStates
from db import async_session
from sqlalchemy.future import select
from models import User
from aiogram.filters import Command, StateFilter

router = Router()

async def set_admin_commands(bot: Bot):
    """
    Устанавливает список команд для администратора.
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


@router.message(Command(commands=['getusers']), StateFilter(AdminStates.AUTHENTICATED_ADMIN))
async def get_users_handler(message: types.Message, state: FSMContext):
    """
    Обработчик команды /getusers для получения списка пользователей.
    """
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
        await set_admin_commands(message.bot)


@router.message(Command(commands=['home']), StateFilter(AdminStates.AUTHENTICATED_ADMIN))
async def admin_home(message: types.Message, state: FSMContext):
    """
    Обработчик команды /home для возврата в меню администратора.
    """
    await message.answer("🏠 Вы вернулись в меню администратора.", reply_markup=get_admin_inline_keyboard())
    await state.set_state(AdminStates.AUTHENTICATED_ADMIN)
    logging.info(f"Администратор {message.from_user.id} вернулся в меню.")
    await set_admin_commands(message.bot)


@router.message(Command(commands=['load_embeddings']), StateFilter(AdminStates.AUTHENTICATED_ADMIN))
async def load_embeddings_handler(message: types.Message, state: FSMContext):
    """
    Обработчик команды /load_embeddings для загрузки эмбеддингов из всех txt файлов в S3 бакете.
    """
    token = config.IAM_TOKEN
    folder_id = config.FOLDER_ID
    embeddings_endpoint = f"{config.RAG_API_URL}/embeddings"

    try:
        response = s3.list_objects_v2(Bucket=config.bucket_name_db)
        files = response.get('Contents', [])
        if not files:
            await message.answer("❌ В бакете нет файлов для загрузки эмбеддингов.")
            logging.info("В бакете нет файлов для загрузки эмбеддингов.")
            return

        txt_files = [file['Key'] for file in files if file['Key'].endswith('.txt')]
        if not txt_files:
            await message.answer("❌ В бакете нет файлов .txt для загрузки эмбеддингов.")
            logging.info("В бакете нет файлов .txt для загрузки эмбеддингов.")
            return

        logging.info(f"Найдено {len(txt_files)} файлов .txt для загрузки эмбеддингов: {txt_files}")
    except Exception as e:
        logging.error(f"Ошибка при получении списка файлов из бакета: {e}")
        await message.answer("❌ Произошла ошибка при получении списка файлов из бакета.")
        return

    for txt_file in txt_files:
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                local_path = os.path.join(temp_dir, os.path.basename(txt_file))
                logging.info(f"Загрузка файла {txt_file} с сервера S3.")
                with open(local_path, "wb") as file:
                    s3.download_fileobj(config.bucket_name_db, txt_file, file)

                logging.info(f"Файл {txt_file} успешно загружен в {local_path}.")
                payload = {
                    "model_name": "distiluse-base-multilingual-cased-v1",
                    "txt_path": local_path
                }

                logging.info(f"Отправка файла {txt_file} на векторизацию.")
                async with aiohttp.ClientSession() as session:
                    async with session.post(embeddings_endpoint, json=payload) as response:
                        if response.status == 200:
                            logging.info(f"Эмбеддинги для файла {txt_file} успешно загружены.")
                        else:
                            response_text = await response.text()
                            logging.error(
                                f"Ошибка при загрузке эмбеддингов для {txt_file}: {response.status}, ответ: {response_text}")
                            await message.answer(f"❌ Ошибка при загрузке эмбеддингов для файла {txt_file}.")
        except NoCredentialsError:
            logging.error("Ошибка доступа к S3. Проверьте ключи доступа.")
            await message.answer("❌ Ошибка доступа к S3. Проверьте ключи доступа.")
        except Exception as e:
            logging.error(f"Ошибка при обработке файла {txt_file}: {e}")
            await message.answer(f"❌ Ошибка при обработке файла {txt_file}.")

    await message.answer("✅ Все файлы .txt обработаны.")
    await set_admin_commands(message.bot)


@router.message(Command(commands=['showembeddings']), StateFilter(AdminStates.AUTHENTICATED_ADMIN))
async def show_embeddings_handler(message: types.Message, state: FSMContext):
    """
    Обработчик команды /showembeddings для просмотра загруженных эмбеддингов.
    Администратор может запросить документы, которые находятся в базе Chroma.
    """
    try:
        # Инициализация клиента Chroma и подключение к коллекции
        knowledge_base = initialize_chroma_client(
            collection_name="knowledge_base",
            persist_directory="/Users/nickstanchenkov/FD_bot_v3/FD_bot_v3/utils"  # Укажите актуальный путь
        )

        # Получение документов из коллекции
        result = get_documents_from_chroma(knowledge_base)

        # Проверка на наличие документов
        if not result:
            await message.answer("🗂 Коллекция пуста или произошла ошибка при получении данных.")
            return

        # Форматирование документов для вывода
        documents = result.get("documents", [])
        formatted_docs = "\n\n".join([
            f"📄 <b>Документ:</b> {doc[:200]}..."  # Показываются только первые 200 символов текста документа
            for doc in documents
        ])

        await message.answer(f"🗂 <b>Документы в коллекции:</b>\n\n{formatted_docs}", parse_mode="HTML")
        logging.info(f"Администратор {message.from_user.id} запросил просмотр эмбеддингов.")
    except Exception as e:
        logging.error(f"Ошибка при получении эмбеддингов: {e}")
        await message.answer("❌ Ошибка при просмотре эмбеддингов. Попробуйте позже.")
    finally:
        await set_admin_commands(message.bot)


@router.message(Command(commands=['clear_chroma']), StateFilter(AdminStates.AUTHENTICATED_ADMIN))
async def clear_chroma_handler(message: types.Message, state: FSMContext):
    """
    Обработчик команды /clear_chroma для очистки коллекции Chroma.
    Администратор может очистить коллекцию данных из базы Chroma.
    """
    try:
        # Инициализация клиента Chroma и подключение к коллекции
        knowledge_base = initialize_chroma_client(
            collection_name="knowledge_base",
            persist_directory="/Users/nickstanchenkov/FD_bot_v3/FD_bot_v3/utils"  # Укажите актуальный путь
        )

        # Очистка коллекции
        clear_chroma_collection(knowledge_base)

        await message.answer("🗑️ Коллекция Chroma успешно очищена.")
        logging.info(f"Администратор {message.from_user.id} очистил коллекцию Chroma.")
    except Exception as e:
        logging.error(f"Ошибка при очистке коллекции Chroma администратором {message.from_user.id}: {e}")
        await message.answer("❌ Произошла ошибка при очистке коллекции. Попробуйте позже.")
    finally:
        await set_admin_commands(message.bot)


@router.message(Command(commands=['uploadtxt']), StateFilter(AdminStates.AUTHENTICATED_ADMIN))
async def upload_txt_handler(message: Message, state: FSMContext):
    """
    Обработчик команды /uploadtxt для загрузки txt файлов в S3.
    Администратор может загружать файлы в облачное хранилище.
    """
    await message.answer("📄 Пожалуйста, отправьте txt файл для загрузки в облако.")
    await state.set_state(AdminStates.WAITING_FOR_FILE)
    logging.info(f"Администратор {message.from_user.id} запросил загрузку txt файла.")


@router.message(StateFilter(AdminStates.WAITING_FOR_FILE), F.document)
async def handle_txt_upload(message: Message, state: FSMContext):
    """
    Обработчик для загрузки файла формата .txt в S3.
    Администратор загружает txt файл, который проверяется на правильность расширения,
    скачивается с сервера Telegram, а затем загружается в облако.
    """
    document = message.document

    # Проверка на расширение файла
    if not document.file_name.endswith('.txt'):
        await message.answer("❌ Пожалуйста, отправьте файл с расширением .txt.")
        logging.info(f"Администратор {message.from_user.id} отправил неподдерживаемый файл: {document.file_name}.")
        return

    try:
        # Загрузка файла с Telegram сервера в оперативную память
        logging.info(f"Загрузка файла {document.file_name} с сервера Telegram.")
        file = io.BytesIO()
        await message.bot.download(document, file)
        file.seek(0)  # Возвращаем курсор в начало файла

        # Загрузка файла в S3
        s3_url = await upload_to_s3_db(file, config.bucket_name_db, document.file_name)

        if s3_url:
            await message.answer(f"✅ Файл успешно загружен в облако: {s3_url}")
            logging.info(f"Файл {document.file_name} успешно загружен в S3 администратором {message.from_user.id}.")
        else:
            await message.answer("❌ Произошла ошибка при загрузке файла в S3.")
            logging.error(f"Ошибка загрузки файла {document.file_name} в S3 администратором {message.from_user.id}.")

    except Exception as e:
        logging.error(f"Ошибка при загрузке файла {document.file_name}: {e}")
        await message.answer("❌ Произошла ошибка при загрузке файла. Попробуйте позже.")

    # Возвращаем состояние администратора
    await state.set_state(AdminStates.AUTHENTICATED_ADMIN)


@router.message(Command(commands=['listfiles']), StateFilter(AdminStates.AUTHENTICATED_ADMIN))
async def list_files_handler(message: types.Message, state: FSMContext):
    """
    Обработчик команды /listfiles для отображения списка файлов в S3.
    Администратор может просмотреть файлы, загруженные в облачное хранилище.
    """
    try:
        # Получаем список файлов из S3
        response = s3.list_objects_v2(Bucket=config.bucket_name_db)
        files = response.get('Contents', [])

        if not files:
            await message.answer("📂 В бакете нет файлов.")
            logging.info(f"Администратор {message.from_user.id} запросил список файлов, но бакет пуст.")
            return

        # Форматируем список файлов для отображения
        file_list = "\n".join([f"📄 {file['Key']}" for file in files])

        await message.answer(f"📂 <b>Файлы в бакете:</b>\n\n{file_list}", parse_mode="HTML")
        logging.info(f"Администратор {message.from_user.id} запросил список файлов в бакете.")
    except Exception as e:
        logging.error(f"Ошибка при получении списка файлов в бакете: {e}")
        await message.answer("❌ Произошла ошибка при получении списка файлов. Попробуйте позже.")