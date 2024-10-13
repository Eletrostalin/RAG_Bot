-- Создание таблицы users
CREATE TABLE IF NOT EXISTS users (
    telegram_id BIGINT PRIMARY KEY,
    username VARCHAR(30),
    full_name VARCHAR(100),
    is_admin BOOLEAN DEFAULT FALSE
);

-- Создание таблицы tickets
CREATE TABLE IF NOT EXISTS tickets (
    ticket_id SERIAL PRIMARY KEY,
    telegram_id BIGINT NOT NULL,
    creation_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completion_time TIMESTAMP,
    active BOOLEAN DEFAULT TRUE,
    closed_by_user BOOLEAN DEFAULT FALSE,
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_telegram_id FOREIGN KEY (telegram_id) REFERENCES users (telegram_id) ON DELETE CASCADE
);

-- Создание таблицы questions
CREATE TABLE IF NOT EXISTS questions (
    question_id SERIAL PRIMARY KEY,
    telegram_id BIGINT NOT NULL,
    creation_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    ticket_id INTEGER REFERENCES tickets(ticket_id) ON DELETE CASCADE,
    text VARCHAR(3000),
    subject VARCHAR(255),
    CONSTRAINT fk_question_telegram_id FOREIGN KEY (telegram_id) REFERENCES users (telegram_id) ON DELETE CASCADE
);

-- Создание таблицы answers
CREATE TABLE IF NOT EXISTS answers (
    answer_id SERIAL PRIMARY KEY,
    ticket_id INTEGER NOT NULL REFERENCES tickets(ticket_id) ON DELETE CASCADE,
    telegram_id BIGINT NOT NULL,
    answer_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    text VARCHAR(3000),
    CONSTRAINT fk_answer_telegram_id FOREIGN KEY (telegram_id) REFERENCES users (telegram_id) ON DELETE CASCADE
);

-- Создание таблицы media_files
CREATE TABLE IF NOT EXISTS media_files (
    id SERIAL PRIMARY KEY,
    file_url VARCHAR NOT NULL,
    file_type VARCHAR NOT NULL,
    filename VARCHAR NOT NULL,
    question_id INTEGER REFERENCES questions(question_id) ON DELETE CASCADE,
    answer_id INTEGER REFERENCES answers(answer_id) ON DELETE CASCADE,
    ticket_id INTEGER REFERENCES tickets(ticket_id) ON DELETE CASCADE
);

-- Создание таблицы migrations
CREATE TABLE IF NOT EXISTS migrations (
    id SERIAL PRIMARY KEY,
    migration_name VARCHAR(255) NOT NULL UNIQUE,
    applied_on TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);