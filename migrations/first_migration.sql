-- Создание таблицы пользователей
CREATE TABLE IF NOT EXISTS users (
    user_id SERIAL PRIMARY KEY,
    telegram_id INTEGER UNIQUE,
    first_name VARCHAR(30),
    second_name VARCHAR(30),
    phone_hash VARCHAR NOT NULL UNIQUE,
    is_admin BOOLEAN DEFAULT FALSE
);

-- Создание таблицы тикетов
CREATE TABLE IF NOT EXISTS tickets (
    ticket_id SERIAL PRIMARY KEY,
    telegram_id INTEGER NOT NULL,
    creation_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completion_time TIMESTAMP,
    active BOOLEAN DEFAULT TRUE,
    closed_by_user BOOLEAN DEFAULT FALSE,
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Создание таблицы вопросов
CREATE TABLE IF NOT EXISTS questions (
    question_id SERIAL PRIMARY KEY,
    telegram_id INTEGER NOT NULL,
    creation_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    ticket_id INTEGER REFERENCES tickets(ticket_id),
    text VARCHAR(3000),
    subject VARCHAR(255)
);

-- Создание таблицы ответов
CREATE TABLE IF NOT EXISTS answers (
    answer_id SERIAL PRIMARY KEY,
    ticket_id INTEGER REFERENCES tickets(ticket_id) NOT NULL,
    telegram_id INTEGER NOT NULL,
    answer_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    text VARCHAR(3000)
);

-- Создание таблицы миграций
CREATE TABLE IF NOT EXISTS migrations (
    id SERIAL PRIMARY KEY,
    migration_name VARCHAR(255) NOT NULL UNIQUE,
    applied_on TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Добавление нового пользователя
INSERT INTO users (telegram_id, first_name, second_name, phone_hash, is_admin) VALUES
(218187166, 'Никита', 'Станченков', '0d0f120d6e7ae16336f1d079d8c33a3de755c5a04e3f9edf2ea35e4531dc3139', TRUE);
