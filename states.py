from aiogram.fsm.state import State, StatesGroup

# Определение классов состояний для управления пользователем и администратором

class QuestionStates(StatesGroup):
    """Состояния для процесса добавления вопроса пользователем."""
    WAITING_FOR_SUBJECT = State()  # Ожидание ввода темы вопроса
    WAITING_FOR_QUESTION = State()  # Ожидание ввода самого вопроса


class UserStates(StatesGroup):
    """Состояния для управления действиями пользователя."""
    AUTHENTICATED_USER = State()  # Пользователь аутентифицирован
    WAITING_FOR_RESPONSE = State()  # Ожидание ответа от администратора
    VIEW_TICKET = State()  # Пользователь просматривает тикет


class AdminStates(StatesGroup):
    """Состояния для управления действиями администратора."""
    AUTHENTICATED_ADMIN = State()  # Администратор аутентифицирован
    WAITING_FOR_RESPONSE = State()  # Ожидание ответа от пользователя или системы
    VIEW_TICKET = State()  # Администратор просматривает тикет
    WAITING_FOR_FILE = State()  # Ожидание загрузки файла (например, медиа)


class UserTicketStates(StatesGroup):
    """Состояния для управления тикетами пользователя."""
    WAITING_FOR_RESPONSE = State()  # Ожидание ответа от администратора
    VIEW_TICKET = State()  # Просмотр тикета
    WAITING_FOR_ADDITIONAL_RESPONSE = State()  # Ожидание дополнительного ответа