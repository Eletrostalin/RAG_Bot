import logging
import io
import boto3
import aiohttp
from PIL import Image
from aiogram import Bot
from aiogram.types import BufferedInputFile
from botocore.exceptions import NoCredentialsError
from config import AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, S3_ENDPOINT_URL, S3_BUCKET_NAME, bucket_name_db

MAX_IMAGE_SIZE_MB = 3
ALLOWED_IMAGE_FORMATS = ['jpg', 'JPEG', 'png']

# Инициализация клиента S3 с указанием хранилища Яндекса
s3 = boto3.client(
    's3',
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    endpoint_url=S3_ENDPOINT_URL  # Указываем URL хранилища Яндекса
)


async def upload_to_s3(file_obj, bucket_name, filename):
    """
    Асинхронно загружает файл в S3.

    Args:
        file_obj (BytesIO): Объект файла для загрузки.
        bucket_name (str): Название бакета S3.
        filename (str): Имя файла.

    Returns:
        str: URL загруженного файла или None при ошибке.
    """
    try:
        s3.upload_fileobj(file_obj, bucket_name, filename)
        file_url = f"{S3_ENDPOINT_URL}/{bucket_name}/{filename}"
        return file_url
    except NoCredentialsError:
        logging.error("Ошибка доступа к Яндекс S3. Проверьте ключи доступа.")
        return None


async def upload_to_s3_db(file_obj, bucket_name, filename):
    """
    Загрузка файлов для базы данных в S3.

    Args:
        file_obj (BytesIO): Файл для загрузки.
        bucket_name (str): Бакет для базы данных.
        filename (str): Имя файла.

    Returns:
        str: URL загруженного файла или None при ошибке.
    """
    try:
        s3.upload_fileobj(file_obj, bucket_name, filename)
        file_url = f"{S3_ENDPOINT_URL}/{bucket_name_db}/{filename}"
        return file_url
    except NoCredentialsError:
        logging.error("Ошибка доступа к Яндекс S3. Проверьте ключи доступа.")
        return None


async def validate_and_compress_media(media_files, message):
    """
    Валидация и сжатие изображений.

    Args:
        media_files (list): Список медиафайлов для валидации и сжатия.
        message (Message): Сообщение для отправки предупреждений.

    Returns:
        list: Список валидных медиафайлов.
    """
    valid_media = []

    for media_file in media_files:
        file_content = media_file.get('file')
        filename = media_file.get('filename')

        try:
            # Открываем файл как изображение для проверки
            image = Image.open(io.BytesIO(file_content.getvalue()))
            image.verify()  # Проверяем, что файл является изображением
            image = Image.open(io.BytesIO(file_content.getvalue()))  # Открываем для манипуляций
            image_size_mb = len(file_content.getvalue()) / (1024 * 1024)

            # Сжатие изображения, если оно превышает лимит
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
            await message.reply(f"Файл {filename} не поддерживается или поврежден. "
                                "Пожалуйста, отправьте изображение формата JPG, PNG.")
            continue

    return valid_media


async def send_file_from_url(bot: Bot, chat_id: int, file_url: str):
    """
    Отправляет файл из URL в чат.

    Args:
        bot (Bot): Экземпляр бота.
        chat_id (int): ID чата для отправки файла.
        file_url (str): URL файла для отправки.
    """
    try:
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