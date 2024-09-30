DROP TABLE IF EXISTS media_files CASCADE;
DROP TABLE IF EXISTS answers CASCADE;
DROP TABLE IF EXISTS questions CASCADE;
DROP TABLE IF EXISTS tickets CASCADE;
DROP TABLE IF EXISTS users CASCADE;

-- Создание таблицы пользователей
CREATE TABLE users (
    telegram_id BIGINT PRIMARY KEY,
    username VARCHAR(50),  -- Увеличиваем длину username до 50 символов
    full_name VARCHAR(100),
    is_admin BOOLEAN DEFAULT FALSE
);

-- Создание таблицы тикетов
CREATE TABLE tickets (
    ticket_id SERIAL PRIMARY KEY,
    telegram_id BIGINT NOT NULL REFERENCES users(telegram_id),
    creation_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completion_time TIMESTAMP,
    active BOOLEAN DEFAULT TRUE,
    closed_by_user BOOLEAN DEFAULT FALSE,
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Создание таблицы вопросов
CREATE TABLE questions (
    question_id SERIAL PRIMARY KEY,
    telegram_id BIGINT NOT NULL REFERENCES users(telegram_id),
    creation_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    ticket_id INTEGER REFERENCES tickets(ticket_id) ON DELETE CASCADE,
    text VARCHAR(3000),
    subject VARCHAR(255)
);

-- Создание таблицы ответов
CREATE TABLE answers (
    answer_id SERIAL PRIMARY KEY,
    ticket_id INTEGER NOT NULL REFERENCES tickets(ticket_id) ON DELETE CASCADE,
    telegram_id BIGINT NOT NULL REFERENCES users(telegram_id),
    answer_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    text VARCHAR(3000)
);

-- Создание таблицы медиафайлов
CREATE TABLE media_files (
    id SERIAL PRIMARY KEY,
    file_url VARCHAR(1024) NOT NULL,
    file_type VARCHAR(10),  -- Тип файла ограничиваем 10 символами
    filename VARCHAR(255),
    question_id INTEGER REFERENCES questions(question_id) ON DELETE CASCADE,
    answer_id INTEGER REFERENCES answers(answer_id) ON DELETE CASCADE,
    ticket_id INTEGER REFERENCES tickets(ticket_id) ON DELETE CASCADE  -- Добавляем связь с тикетом
);