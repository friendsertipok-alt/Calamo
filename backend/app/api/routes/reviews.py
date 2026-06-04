from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List
from app.database import get_db
from app import models, schemas
from app.auth import get_current_user
from datetime import datetime

router = APIRouter()

@router.get("/", response_model=List[schemas.review.Review])
async def get_reviews(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(models.Review).filter(models.Review.is_hidden == False).order_by(models.Review.created_at.desc()))
    return result.scalars().all()

from app.utils.security import sanitize_html

@router.post("/", response_model=schemas.review.Review)
async def create_review(
    review: schemas.review.ReviewCreate,
    user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    db_review = models.Review(
        user_id=user.id,
        user_name=sanitize_html(review.user_name) or user.full_name,
        text=sanitize_html(review.text),
        rating=review.rating
    )
    db.add(db_review)
    await db.commit()
    await db.refresh(db_review)
    return db_review

from app.auth import get_current_admin

@router.delete("/{review_id}")
async def delete_review(
    review_id: int, 
    admin: dict = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(models.Review).filter(models.Review.id == review_id))
    db_review = result.scalars().first()
    if not db_review:
        raise HTTPException(status_code=404, detail="Review not found")
    
    await db.delete(db_review)
    await db.commit()
    return {"status": "deleted"}

@router.patch("/{review_id}", response_model=schemas.review.Review)
async def update_review(
    review_id: int,
    update_data: schemas.review.ReviewUpdate,
    admin: dict = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(models.Review).filter(models.Review.id == review_id))
    db_review = result.scalars().first()
    if not db_review:
        raise HTTPException(status_code=404, detail="Review not found")
        
    if update_data.user_name is not None:
        db_review.user_name = update_data.user_name
    if update_data.text is not None:
        db_review.text = update_data.text
    if update_data.rating is not None:
        db_review.rating = update_data.rating
    if update_data.created_at is not None:
        db_review.created_at = update_data.created_at
        
    await db.commit()
    await db.refresh(db_review)
    return db_review
