# Telegram Support Bot with Simple RAG System

This is a support bot for Telegram with an integrated simple RAG (Retrieval-Augmented Generation) system. The project is live in production and was developed as a commissioned work, so some decisions were made to suit business needs, such as user registration.

## About the Project

The system only adds users who are either present or added to the corporate chat. The RAG system is calibrated for a small test knowledge base.

Vectors are stored in Chroma DB. If the bot doesn't find a match for the vectors, it creates a ticket in the relational database. Administrators in the chat can reply to this ticket via private messages with the bot, and the response is sent back to the user.

## Tech Stack

- **Bot Framework**: aiogram 3.10
- **Backend**: FastAPI
- **Database**: PostgreSQL
- **Vector Database**: Chroma DB
- **RAG System**: Langchain, Yandex GPT
- **ORM**: SQLAlchemy
