import logging
import html
from config import IAM_TOKEN, FOLDER_ID, CHROMA_PERSIST_DIR
from db import add_question
from http.client import HTTPException
from fastapi import HTTPException, FastAPI, Body
from langchain_core.documents import Document
from langchain.chains.combine_documents.stuff import create_stuff_documents_chain
from langchain_core.prompts.prompt import PromptTemplate
from langchain_community.llms import YandexGPT
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer

from chains.chroma_utils import initialize_chroma_client, add_documents_to_chroma, search_similar_docs

app = FastAPI()

class Query(BaseModel):
    text: str


def load_text_file(file_path: str) -> str:
    """
    Загружает текст из файла и возвращает его содержимое.

    :param file_path: Путь к текстовому файлу.
    :return: Содержимое файла в виде строки.
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            return file.read()
    except FileNotFoundError:
        logging.error(f"Файл не найден: {file_path}")
        raise HTTPException(status_code=404, detail="Файл не найден")


def split_text_into_chunks(text: str, chunk_size: int = 500, chunk_overlap: int = 70) -> list:
    """
    Разбивает текст на чанки для последующей обработки.

    :param text: Исходный текст.
    :param chunk_size: Размер чанка.
    :param chunk_overlap: Перекрытие чанков.
    :return: Список чанков текста.
    """
    splitter = RecursiveCharacterTextSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    chunks = splitter.split_text(text)
    return [{"id": f"chunk_{i + 1}", "text": chunk} for i, chunk in enumerate(chunks)]


def get_hf_embeddings(model_name: str = "distiluse-base-multilingual-cased-v1") -> SentenceTransformer:
    """
    Загружает модель HuggingFace для создания эмбеддингов.

    :param model_name: Название модели.
    :return: Модель SentenceTransformer.
    """
    logging.info(f"Загружается модель: {model_name}")
    return SentenceTransformer(model_name)


@app.post("/embeddings")
async def load_embeddings(
        model_name: str = Body("distiluse-base-multilingual-cased-v1", embed=True),
        txt_path: str = Body(..., embed=True)
):
    """
    Обработчик для создания эмбеддингов из текстового файла и их сохранения в Chroma.

    :param model_name: Модель для создания эмбеддингов.
    :param txt_path: Путь к текстовому файлу.
    """
    logging.info(f"Получен запрос на /embeddings с параметрами: model_name={model_name}, txt_path={txt_path}")
    try:
        text = load_text_file(txt_path)
        logging.info(f"Текст успешно загружен из {txt_path}, длина текста: {len(text)} символов.")
        chunks = split_text_into_chunks(text)
        logging.info(f"Текст разбит на {len(chunks)} чанков.")

        model = get_hf_embeddings(model_name)
        logging.info("Модель для эмбеддингов успешно загружена.")

        # Инициализация Chroma и добавление документов
        knowledge_base = initialize_chroma_client(
            collection_name="knowledge_base",
            persist_directory=CHROMA_PERSIST_DIR  # Используем путь из переменной окружения
        )
        add_documents_to_chroma(knowledge_base, chunks, model)

        logging.info("Документы успешно добавлены в Chroma.")
    except Exception as e:
        logging.error(f"Ошибка при выполнении загрузки эмбеддингов: {e}")
        raise HTTPException(status_code=500, detail=f"Ошибка при загрузке эмбеддингов: {e}")


def generate_response_with_gpt(token: str, folder_id: str, query_text: str, context: list, token_limit: int = 100):
    """
    Генерация ответа с использованием Yandex GPT на основе запроса и контекста.

    :param token: IAM токен для Yandex GPT.
    :param folder_id: ID папки Yandex.
    :param query_text: Текст запроса пользователя.
    :param context: Контекст для запроса.
    :param token_limit: Лимит на количество токенов в ответе.
    :return: Ответ от Yandex GPT или предложение уточнить вопрос.
    """
    document_prompt = PromptTemplate(
        input_variables=["page_content"],
        template="{page_content}"
    )

    # Экранирование контекста и запроса
    escaped_context = [html.escape(doc.get('page_content', '')) for doc in context if 'page_content' in doc]
    escaped_query_text = html.escape(query_text)

    # Шаблон для генерации ответа
    stuff_prompt_override = """
        Ты сотрудник техподдержки. Отвечай вежливо и учтиво, опираясь на приложенные к вопросу тексты.

        Постарайся дать ответ, который уместится в {token_limit} токенов. Если требуется большее объяснение, сокращай ответ до самого важного.

        Если ты не можешь полностью уместить ответ, предложи пользователю задать уточняющий вопрос.

        Текст:
        -----
        {context}
        -----
        Вопрос:
        {query}
    """

    prompt = PromptTemplate(
        template=stuff_prompt_override,
        input_variables=["context", "query", "token_limit"]
    )

    llm = YandexGPT(iam_token=token, folder_id=folder_id)

    # Создание цепочки с использованием модели
    chain = create_stuff_documents_chain(
        llm=llm,
        prompt=prompt,
        document_prompt=document_prompt,
        document_variable_name="context"
    )

    input_data = {
        'context': [Document(page_content=doc) for doc in escaped_context],
        'query': escaped_query_text,
        'token_limit': token_limit
    }

    logging.info(f"Данные перед invoke: {input_data}")

    try:
        response = chain.invoke(input_data)

        if response is None:
            return "К сожалению, не удалось найти релевантную информацию. Попробуйте переформулировать вопрос."
    except Exception as e:
        logging.error(f"Ошибка при выполнении запроса к GPT: {e}")
        raise HTTPException(status_code=500, detail="Ошибка при обработке запроса к GPT")

    return response


@app.post("/llm_rag")
async def query_llm_rag(token: str = IAM_TOKEN, folder_id: str = FOLDER_ID, query: Query = Body(...)):
    """
    Обрабатывает запрос к Yandex GPT с использованием Retrieve-And-Generate (RAG).

    :param token: IAM токен для доступа к GPT.
    :param folder_id: Идентификатор папки в Yandex Cloud.
    :param query: Запрос пользователя.
    :return: Ответ GPT, основанный на релевантных документах.
    """
    knowledge_base = initialize_chroma_client(
        collection_name="knowledge_base",
        persist_directory=CHROMA_PERSIST_DIR
    )
    docs = search_similar_docs(knowledge_base, query.text)

    logging.info(f"Найденные документы (docs): {docs}")

    if not docs:
        logging.info("Релевантные документы не найдены, создаем новый тикет.")
        user_id = ...  # Используйте актуальные данные пользователя
        subject = "Автоматически созданный тикет"
        await add_question(user_id, query.text, subject)
        raise HTTPException(status_code=404, detail="Релевантные документы не найдены, создан новый тикет.")

    context = [Document(page_content=doc['text']) for doc in docs if 'text' in doc]
    response = generate_response_with_gpt(token, folder_id, query.text, context)

    return response


def process_search_results(similar_docs):
    """
    Обрабатывает результаты поиска и создает объекты Document.

    Args:
        similar_docs (list): Список похожих документов в формате словарей.

    Returns:
        list: Список объектов Document. Каждый объект содержит `page_content` текста найденного документа.
    """
    processed_docs = []

    for doc in similar_docs:
        # Проверяем, что документ является словарем и содержит ключ 'text'
        if isinstance(doc, dict) and 'text' in doc:
            try:
                # Создаем объект Document с page_content из найденного текста
                processed_docs.append(Document(page_content=doc['text']))
            except Exception as e:
                logging.error(f"Ошибка при создании объекта Document для {doc}: {e}")
        else:
            logging.warning(f"Документ не имеет ожидаемого формата или отсутствует ключ 'text': {doc}")

    # Логируем количество успешно обработанных документов
    logging.info(f"Обработано {len(processed_docs)} документов.")

    return processed_docs