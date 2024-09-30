import logging
from db import add_question
from sentence_transformers import SentenceTransformer
from chromadb import PersistentClient  # Используем PersistentClient для сохранения данных


class EmbeddingFunctionWrapper:
    def __init__(self, model):
        """
        Обертка для модели эмбеддингов.

        :param model: Модель для генерации эмбеддингов.
        """
        self.model = model

    def __call__(self, input):
        """
        Метод для преобразования входных данных в эмбеддинги.

        :param input: Входной текст (список строк).
        :return: Эмбеддинги.
        """
        return self.model.encode(input)


def initialize_chroma_client(collection_name: str, persist_directory: str):
    """
    Инициализация клиента Chroma и подключение к коллекции с указанием директории хранения данных.

    :param collection_name: Название коллекции Chroma.
    :param persist_directory: Директория для сохранения данных.
    :return: Коллекция Chroma.
    """
    chroma_client = PersistentClient(path=persist_directory)

    # Загрузка модели для эмбеддингов
    model = SentenceTransformer("distiluse-base-multilingual-cased-v1")

    # Обертывание модели для использования в Chroma
    embedding_function = EmbeddingFunctionWrapper(model)

    # Создание или получение коллекции в Chroma
    collection = chroma_client.get_or_create_collection(
        name=collection_name,
        embedding_function=embedding_function,
        metadata={"description": "Коллекция для хранения эмбеддингов и текстов из базы знаний"}
    )

    return collection


def add_documents_to_chroma(knowledge_base, chunks, model: SentenceTransformer):
    """
    Векторизация текста и сохранение в Chroma.

    :param knowledge_base: Коллекция Chroma.
    :param chunks: Разделенный на части текст для добавления.
    :param model: Модель для генерации эмбеддингов.
    """
    documents = [chunk["text"] for chunk in chunks]
    ids = [chunk["id"] for chunk in chunks]

    logging.info("Начало генерации эмбеддингов...")

    try:
        # Генерация эмбеддингов
        embeddings_list = model.encode(documents)
        logging.info(f"Эмбеддинги сгенерированы. Количество: {len(embeddings_list)}.")
    except Exception as e:
        logging.error(f"Ошибка при генерации эмбеддингов: {e}")
        return

    # Преобразование эмбеддингов в списки
    embeddings = [embedding.tolist() for embedding in embeddings_list]

    try:
        # Добавление документов и эмбеддингов в Chroma
        if all(isinstance(embed, list) and len(embed) > 0 for embed in embeddings):
            knowledge_base.upsert(
                ids=ids,
                documents=documents,
                embeddings=embeddings
            )
            logging.info("Документы успешно добавлены в Chroma.")
        else:
            logging.error("Некоторые эмбеддинги невалидны.")
    except Exception as e:
        logging.error(f"Ошибка при добавлении документов в Chroma: {e}")


def get_documents_from_chroma(knowledge_base, limit=10):
    """
    Извлечение документов и эмбеддингов из Chroma.

    :param knowledge_base: Коллекция Chroma.
    :param limit: Количество документов для извлечения.
    :return: Список документов и их идентификаторов.
    """
    try:
        result = knowledge_base.get(
            include=["documents", "metadatas"],
            limit=limit
        )

        if result is None:
            logging.error("Получен пустой результат из Chroma.")
            return {"documents": [], "metadatas": []}

        documents = result.get("documents", [])
        metadatas = result.get("metadatas", [])

        logging.info(f"Извлечено {len(documents)} документов из Chroma.")

        return {"documents": documents, "metadatas": metadatas}
    except Exception as e:
        logging.error(f"Ошибка при извлечении документов из Chroma: {e}")
        return {"documents": [], "metadatas": []}


async def search_similar_docs(knowledge_base, query_text, k=3, threshold=1.3, user_id=None, subject="Новый вопрос",
                              from_user=None):
    """
    Поиск похожих документов в Chroma на основе текстового запроса.

    :param knowledge_base: Коллекция Chroma для поиска.
    :param query_text: Текст запроса для поиска.
    :param k: Количество результатов для извлечения.
    :param threshold: Порог для фильтрации релевантных документов.
    :param user_id: ID пользователя для создания тикета.
    :param subject: Тема вопроса для тикета.
    :param from_user: Объект пользователя Telegram.
    :return: Список документов, наиболее похожих на запрос.
    """
    try:
        results = knowledge_base.query(
            query_texts=[query_text],
            n_results=k,
            include=["documents", "metadatas", "distances"]
        )

        logging.info(f"Результаты поиска от Chroma: {results}")

        if not results or not results.get('documents'):
            logging.info("Похожие документы не найдены.")
            return []

        logging.info(f"Найдено {len(results['documents'])} похожих документов.")

        processed_docs = []
        for idx, (doc_list, dist_list) in enumerate(zip(results['documents'], results['distances'])):
            for doc, distance in zip(doc_list, dist_list):
                metadata = results.get('metadatas', [[]])[idx]

                logging.info(f"Документ {idx + 1}: {doc}, Метаданные: {metadata}, Дистанция: {distance}")

                if distance > threshold:
                    logging.info(f"Документ {idx + 1} не прошел порог релевантности. Дистанция: {distance}")
                    continue

                if isinstance(doc, str):
                    processed_docs.append({"text": doc})
                else:
                    logging.warning(f"Документ {idx + 1} не имеет ожидаемого формата.")

        if not processed_docs:
            logging.info("Не найдено релевантных документов после фильтрации.")
            if user_id is not None:
                new_question = await add_question(
                    user_id=user_id,
                    question_text=query_text,
                    subject=subject,
                    from_user=from_user
                )
                logging.info(f"Создан новый тикет с ID: {new_question.ticket_id} для пользователя {user_id}")
                return []

        return processed_docs
    except Exception as e:
        logging.error(f"Ошибка при поиске похожих документов: {e}")
        return []


def clear_chroma_collection(knowledge_base):
    """
    Очистка коллекции Chroma.

    :param knowledge_base: Коллекция Chroma.
    """
    try:
        all_items = knowledge_base.get(ids=None)

        if 'ids' in all_items:
            all_ids = all_items['ids']
            if all_ids:
                knowledge_base.delete(ids=all_ids)
                logging.info("Все элементы коллекции Chroma успешно удалены.")
            else:
                logging.info("Коллекция Chroma уже пуста.")
        else:
            logging.error("Ошибка: Неверная структура данных, ожидался ключ 'ids'.")
    except Exception as e:
        logging.error(f"Ошибка при удалении данных коллекции Chroma: {e}")