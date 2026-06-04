from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Text, Boolean
from sqlalchemy.orm import relationship
from datetime import datetime
from .database import Base

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    full_name = Column(String)
    avatar_url = Column(String, nullable=True)
    balance = Column(Float, default=0.0)
    is_admin = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Провайдеры входа (google, apple)
    provider = Column(String, default="google")
    provider_id = Column(String, unique=True, index=True)

    orders = relationship("Order", back_populates="owner")
    transactions = relationship("Transaction", back_populates="user")
    visits = relationship("UserVisit", back_populates="user")

class UserVisit(Base):
    __tablename__ = "user_visits"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    is_new_user = Column(Boolean, default=False)
    visited_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="visits")

class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    
    topic = Column(String, nullable=False)
    work_type = Column(String)
    status = Column(String, default="pending") # pending, processing, completed, failed
    
    price = Column(Float)
    file_path = Column(String, nullable=True)
    
    # Храним метаданные заказа в JSON или тексте
    details = Column(Text, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    
    owner = relationship("User", back_populates="orders")

class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    
    amount = Column(Float, nullable=False)
    type = Column(String) # top-up, deposit, withdrawal
    status = Column(String, default="completed")
    external_id = Column(String, nullable=True, unique=True, index=True) # ID из платежной системы (Идемпотентность)
    description = Column(String, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    
    user = relationship("User", back_populates="transactions")

class Review(Base):
    __tablename__ = "reviews"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    
    user_name = Column(String) # Имя для отображения
    text = Column(Text, nullable=False)
    rating = Column(Integer, default=5)
    
    is_hidden = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    owner = relationship("User", back_populates="reviews")

User.reviews = relationship("Review", back_populates="owner")

class SupportTicket(Base):
    __tablename__ = "support_tickets"

    id = Column(Integer, primary_key=True, index=True)
    user_email = Column(String, nullable=False)
    message = Column(Text, nullable=False)
    status = Column(String, default="Новая") # Новая, В работе, Закрыта
    file_urls = Column(Text, nullable=True) # JSON list of URLs
    created_at = Column(DateTime, default=datetime.utcnow)

class TelegramMessageMapping(Base):
    __tablename__ = "telegram_message_mappings"

    id = Column(Integer, primary_key=True, index=True)
    # ID сообщения, которое бот отправил в админ-группу
    admin_message_id = Column(Integer, unique=True, index=True)
    # ID пользователя в Telegram, который отправил исходное сообщение
    user_chat_id = Column(Integer, nullable=False)
    # Имя пользователя
    user_name = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class LLMUsage(Base):
    __tablename__ = "llm_usage"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(String, index=True, nullable=True) # ID заказа из JSON хранилища
    model = Column(String, nullable=False)
    prompt_tokens = Column(Integer, default=0)
    completion_tokens = Column(Integer, default=0)
    total_tokens = Column(Integer, default=0)
    # Ориентировочная стоимость в рублях
    estimated_cost_rub = Column(Float, default=0.0)
    # Описание (например, "Генерация плана", "Глава 1")
    description = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class BlacklistedToken(Base):
    __tablename__ = "blacklisted_tokens"

    id = Column(Integer, primary_key=True, index=True)
    token = Column(String, unique=True, index=True, nullable=False)
    blacklisted_at = Column(DateTime, default=datetime.utcnow)
