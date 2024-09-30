-- Удаление таблиц с каскадным удалением данных
DROP TABLE IF EXISTS media_files CASCADE;
DROP TABLE IF EXISTS answers CASCADE;
DROP TABLE IF EXISTS questions CASCADE;
DROP TABLE IF EXISTS tickets CASCADE;
DROP TABLE IF EXISTS users CASCADE;
DROP TABLE IF EXISTS migrations CASCADE;

-- Создание таблицы пользователей
CREATE TABLE users (
    telegram_id BIGINT PRIMARY KEY,
    username VARCHAR(30),  -- Уникальное имя пользователя в Telegram
    full_name VARCHAR(100),  -- Полное имя пользователя
    is_admin BOOLEAN DEFAULT FALSE
);

-- Создание таблицы тикетов
CREATE TABLE tickets (
    ticket_id SERIAL PRIMARY KEY,
    telegram_id BIGINT NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
    creation_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completion_time TIMESTAMP,
    active BOOLEAN DEFAULT TRUE,
    closed_by_user BOOLEAN DEFAULT FALSE,  -- Новое поле для определения, закрыт ли тикет пользователем
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Создание таблицы вопросов
CREATE TABLE questions (
    question_id SERIAL PRIMARY KEY,
    telegram_id BIGINT NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
    creation_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    ticket_id INTEGER REFERENCES tickets(ticket_id) ON DELETE CASCADE,
    text VARCHAR(3000),  -- Текст вопроса
    subject VARCHAR(255)  -- Тема вопроса
);

-- Создание таблицы ответов
CREATE TABLE answers (
    answer_id SERIAL PRIMARY KEY,
    ticket_id INTEGER NOT NULL REFERENCES tickets(ticket_id) ON DELETE CASCADE,
    telegram_id BIGINT NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
    answer_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    text VARCHAR(3000)  -- Текст ответа
);

-- Создание таблицы медиафайлов
CREATE TABLE media_files (
    id SERIAL PRIMARY KEY,
    file_url VARCHAR(1024) NOT NULL,  -- URL файла
    file_type VARCHAR(10) NOT NULL,  -- Тип файла (например, jpg, png, mp4 и т.д.)
    filename VARCHAR(255) NOT NULL,  -- Имя файла
    question_id INTEGER REFERENCES questions(question_id) ON DELETE CASCADE,
    answer_id INTEGER REFERENCES answers(answer_id) ON DELETE CASCADE,
    ticket_id INTEGER REFERENCES tickets(ticket_id) ON DELETE CASCADE
);

-- Создание таблицы миграций
CREATE TABLE migrations (
    id SERIAL PRIMARY KEY,
    migration_name VARCHAR(255) UNIQUE NOT NULL,
    applied_on TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);