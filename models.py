from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, DateTime, BigInteger
from sqlalchemy.orm import relationship
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime

Base = declarative_base()

class User(Base):
    """Модель пользователя, представляющего участника взаимодействия с ботом."""
    __tablename__ = 'users'

    telegram_id = Column(BigInteger, primary_key=True, unique=True)  # Идентификатор пользователя в Telegram
    username = Column(String(30))  # Уникальное имя пользователя
    full_name = Column(String(100))  # Полное имя пользователя (необязательно)
    is_admin = Column(Boolean, default=False)  # Является ли пользователь администратором

    tickets = relationship('Ticket', back_populates='user')  # Связь с тикетами
    questions = relationship('Question', back_populates='user')  # Связь с вопросами
    answers = relationship('Answer', back_populates='user')  # Связь с ответами


class Ticket(Base):
    """Модель тикета, представляющего вопрос или проблему, отправленную пользователем."""
    __tablename__ = 'tickets'

    ticket_id = Column(Integer, primary_key=True, autoincrement=True)  # Уникальный идентификатор тикета
    telegram_id = Column(BigInteger, ForeignKey('users.telegram_id'), nullable=False)  # ID пользователя, создавшего тикет
    creation_time = Column(DateTime, default=datetime.utcnow)  # Время создания тикета
    completion_time = Column(DateTime)  # Время завершения тикета
    active = Column(Boolean, default=True)  # Активен ли тикет
    closed_by_user = Column(Boolean, default=False)  # Был ли тикет закрыт пользователем
    last_updated = Column(DateTime, default=datetime.utcnow)  # Время последнего обновления тикета

    user = relationship('User', back_populates='tickets')  # Связь с моделью User
    questions = relationship('Question', back_populates='ticket')  # Связь с вопросами
    answers = relationship('Answer', back_populates='ticket')  # Связь с ответами
    media_files = relationship('MediaFile', back_populates='ticket', cascade="all, delete-orphan")  # Связь с медиафайлами


class Question(Base):
    """Модель вопроса, который отправляется пользователем в рамках тикета."""
    __tablename__ = 'questions'

    question_id = Column(Integer, primary_key=True, autoincrement=True)  # Уникальный идентификатор вопроса
    telegram_id = Column(BigInteger, ForeignKey('users.telegram_id'), nullable=False)  # ID пользователя
    creation_time = Column(DateTime, default=datetime.utcnow)  # Время создания вопроса
    ticket_id = Column(Integer, ForeignKey('tickets.ticket_id'))  # Связь с тикетом
    text = Column(String(3000))  # Текст вопроса
    subject = Column(String(255))  # Тема вопроса

    user = relationship('User', back_populates='questions')  # Связь с пользователем
    ticket = relationship('Ticket', back_populates='questions')  # Связь с тикетом
    media_files = relationship('MediaFile', back_populates='question', cascade="all, delete-orphan")  # Связь с медиафайлами


class Answer(Base):
    """Модель ответа, который отправляется администратором в ответ на вопрос пользователя."""
    __tablename__ = 'answers'

    answer_id = Column(Integer, primary_key=True, autoincrement=True)  # Уникальный идентификатор ответа
    ticket_id = Column(Integer, ForeignKey('tickets.ticket_id'), nullable=False)  # ID тикета
    telegram_id = Column(BigInteger, ForeignKey('users.telegram_id'), nullable=False)  # ID пользователя (администратора)
    answer_time = Column(DateTime, default=datetime.utcnow)  # Время отправки ответа
    text = Column(String(3000))  # Текст ответа

    user = relationship('User', back_populates='answers')  # Связь с пользователем
    ticket = relationship('Ticket', back_populates='answers')  # Связь с тикетом
    media_files = relationship('MediaFile', back_populates='answer', cascade="all, delete-orphan")  # Связь с медиафайлами


class MediaFile(Base):
    """Модель для хранения информации о медиафайлах, прикрепленных к вопросам или ответам."""
    __tablename__ = 'media_files'

    id = Column(Integer, primary_key=True)  # Уникальный идентификатор файла
    file_url = Column(String, nullable=False)  # URL файла в облаке
    file_type = Column(String, nullable=False)  # Тип файла (например, изображение или видео)
    filename = Column(String, nullable=False)  # Имя файла
    question_id = Column(Integer, ForeignKey('questions.question_id'), nullable=True)  # Связь с вопросом
    answer_id = Column(Integer, ForeignKey('answers.answer_id'), nullable=True)  # Связь с ответом
    ticket_id = Column(Integer, ForeignKey('tickets.ticket_id'), nullable=True)  # Связь с тикетом

    question = relationship("Question", back_populates="media_files")  # Связь с вопросом
    answer = relationship("Answer", back_populates="media_files")  # Связь с ответом
    ticket = relationship("Ticket", back_populates="media_files")  # Связь с тикетом


class Migration(Base):
    """Модель для хранения информации о миграциях базы данных."""
    __tablename__ = 'migrations'

    id = Column(Integer, primary_key=True, autoincrement=True)  # Уникальный идентификатор миграции
    migration_name = Column(String(255), nullable=False, unique=True)  # Название миграции
    applied_on = Column(DateTime, default=datetime.utcnow)  # Время применения миграции

    def __repr__(self):
        return f"<Migration(name={self.migration_name}, applied_on={self.applied_on})>"