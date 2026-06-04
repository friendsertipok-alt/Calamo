from pydantic import BaseModel, EmailStr
from typing import Optional, List
from datetime import datetime

class UserBase(BaseModel):
    email: EmailStr
    full_name: Optional[str] = None
    avatar_url: Optional[str] = None

class UserResponse(UserBase):
    id: int
    balance: float
    is_admin: bool = False
    created_at: datetime

    class Config:
        from_attributes = True

class Token(BaseModel):
    access_token: str
    token_type: str

class LoginRequest(BaseModel):
    token: str # Токен от Google/Apple
    provider: str # "google" или "apple"
