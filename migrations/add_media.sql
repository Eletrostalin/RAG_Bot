-- 1. Добавляем таблицу media_files для хранения медиафайлов
CREATE TABLE media_files (
    id SERIAL PRIMARY KEY,
    file_url VARCHAR(1024) NOT NULL, -- Ссылка на файл в S3
    file_type VARCHAR(10),           -- Тип файла (image или video)
    filename VARCHAR(255),           -- Имя файла

    -- Внешний ключ на вопрос
    question_id INTEGER REFERENCES questions(question_id) ON DELETE CASCADE,

    -- Внешний ключ на ответ
    answer_id INTEGER REFERENCES answers(answer_id) ON DELETE CASCADE
);

-- 2. Обновляем таблицу questions для добавления связи с media_files
-- (вопросы уже связаны через question_id в таблице media_files, так что в самой таблице questions изменений не нужно)

-- 3. Обновляем таблицу answers для добавления связи с media_files
-- (аналогично, ответ связан через answer_id в таблице media_files)

-- 4. Обновление поля "last_updated" в таблице tickets, чтобы оно обновлялось при изменениях
ALTER TABLE tickets
    ALTER COLUMN last_updated SET DEFAULT CURRENT_TIMESTAMP;

-- 5. (Если еще не было изменений для subject)
-- Добавляем колонку subject для вопросов (если еще не добавлялась)
ALTER TABLE questions
    ADD COLUMN IF NOT EXISTS subject VARCHAR(255);
