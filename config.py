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

# S3 хранилище
AWS_ACCESS_KEY_ID = os.getenv('AWS_ACCESS_KEY_ID')
AWS_SECRET_ACCESS_KEY = os.getenv('AWS_SECRET_ACCESS_KEY')
S3_ENDPOINT_URL = os.getenv('S3_ENDPOINT_URL')
S3_BUCKET_NAME = os.getenv('S3_BUCKET_NAME')
bucket_name_db = os.getenv('S3_BUCKET_NAME_DB')

# IAM токен и другие параметры
IAM_TOKEN = os.getenv("IAM_TOKEN")
FOLDER_ID = os.getenv("FOLDER_ID")
RAG_API_URL = os.getenv("RAG_API_URL")
LLM_RAG_ENDPOINT = os.getenv("LLM_RAG_ENDPOINT")
IAM_TOKEN_PATH = os.getenv("IAM_TOKEN_PATH")
OAUTH_TOKEN = os.getenv("OAUTH_TOKEN")
CHROMA_PERSIST_DIR = os.getenv("CHROMA_PERSIST_DIR")