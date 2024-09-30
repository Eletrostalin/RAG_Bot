import requests
import json
import logging
from config import OAUTH_TOKEN
from dotenv import load_dotenv, set_key

# URL для получения IAM токена
IAM_TOKEN_URL = "https://iam.api.cloud.yandex.net/iam/v1/tokens"

def get_iam_token(oauth_token):
    """Функция для получения IAM токена."""
    logging.info("Попытка получить IAM токен...")
    headers = {"Content-Type": "application/json"}
    payload = {"yandexPassportOauthToken": oauth_token}

    try:
        response = requests.post(IAM_TOKEN_URL, headers=headers, data=json.dumps(payload))
        response.raise_for_status()  # Проверяем на ошибки

        iam_token = response.json().get("iamToken")
        if not iam_token:
            logging.error("Не удалось получить IAM токен. Ответ сервера: %s", response.text)
            return None

        logging.info("IAM токен успешно получен.")
        return iam_token

    except requests.exceptions.RequestException as e:
        logging.error(f"Ошибка при запросе IAM токена: {e}")
        return None

def save_iam_token(iam_token):
    """Сохраняет IAM токен в файл .env."""
    logging.info("Сохранение IAM токена в .env файл...")
    try:
        # Загрузим текущий .env файл
        load_dotenv()

        # Обновим значение IAM_TOKEN в .env
        set_key('.env', 'IAM_TOKEN', iam_token)

        logging.info(f"IAM токен успешно обновлен в .env.")
    except Exception as e:
        logging.error(f"Ошибка при сохранении IAM токена: {e}")

def update_iam_token():
    """Обновление и сохранение IAM токена."""
    logging.info("Обновление IAM токена...")
    token = get_iam_token(OAUTH_TOKEN)
    if token:
        save_iam_token(token)
        logging.info("IAM токен обновлен и сохранен.")
        return token
    else:
        logging.error("Не удалось обновить IAM токен.")
        return None