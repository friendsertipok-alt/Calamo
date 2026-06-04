from pydantic import BaseModel
from datetime import datetime
from typing import Optional

class ReviewBase(BaseModel):
    user_name: str
    text: str
    rating: int = 5

class ReviewCreate(ReviewBase):
    pass

class ReviewUpdate(BaseModel):
    user_name: Optional[str] = None
    text: Optional[str] = None
    rating: Optional[int] = None
    created_at: Optional[datetime] = None

class Review(ReviewBase):
    id: int
    user_id: Optional[int]
    created_at: datetime

    class Config:
        from_attributes = True
