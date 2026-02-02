"""Admin endpoints for user and system management"""

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from sqlalchemy import desc, or_
from typing import List, Optional
from datetime import datetime, timedelta
import secrets

from wazz_shared.database import get_db
from dependencies import get_current_admin_user
from wazz_shared.models import User, GuestSession, AudioProcessingJob, UserUsageStats
from wazz_shared.schemas import (
    AdminUserResponse,
    AdminUserCreate,
    AdminUserUpdate,
    AdminPasswordUpdate,
    AdminPasswordReset,
    MessageResponse
)
from auth import get_password_hash
import os

router = APIRouter(prefix="/admin", tags=["admin"])


# ==================== User Management ====================

@router.get("/users", response_model=List[AdminUserResponse])
async def list_all_users(
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(50, ge=1, le=100, description="Number of records to return"),
    search: Optional[str] = Query(None, description="Search by email or username"),
    is_active: Optional[bool] = Query(None, description="Filter by active status"),
    is_verified: Optional[bool] = Query(None, description="Filter by verified status"),
    is_admin: Optional[bool] = Query(None, description="Filter by admin status"),
    current_admin: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """
    List all users with pagination and filtering

    Admin only endpoint to view all registered users
    """
    query = db.query(User)

    # Apply filters
    if search:
        query = query.filter(
            or_(
                User.email.ilike(f"%{search}%"),
                User.username.ilike(f"%{search}%")
            )
        )

    if is_active is not None:
        query = query.filter(User.is_active == is_active)

    if is_verified is not None:
        query = query.filter(User.is_verified == is_verified)

    if is_admin is not None:
        query = query.filter(User.is_admin == is_admin)

    # Order by most recent first
    query = query.order_by(desc(User.created_at))

    # Paginate
    users = query.offset(skip).limit(limit).all()

    return users


@router.get("/users/{user_id}", response_model=AdminUserResponse)
async def get_user_details(
    user_id: int,
    current_admin: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """
    Get detailed information about a specific user

    Admin only endpoint
    """
    user = db.query(User).filter(User.id == user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return user


@router.post("/users", response_model=AdminUserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    user_data: AdminUserCreate,
    current_admin: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """
    Create a new user as admin

    Admin only endpoint to create users without email verification
    """
    # Check if email already exists
    existing_user = db.query(User).filter(User.email == user_data.email).first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )

    # Check if username already exists
    existing_username = db.query(User).filter(User.username == user_data.username).first()
    if existing_username:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already taken"
        )

    # Hash password
    hashed_password = get_password_hash(user_data.password)

    # Create user
    new_user = User(
        email=user_data.email,
        username=user_data.username,
        hashed_password=hashed_password,
        is_admin=user_data.is_admin,
        is_verified=user_data.is_verified,
        is_active=user_data.is_active
    )

    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    return new_user


@router.patch("/users/{user_id}", response_model=AdminUserResponse)
async def update_user(
    user_id: int,
    user_data: AdminUserUpdate,
    current_admin: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """
    Update user details

    Admin only endpoint to update user information
    """
    user = db.query(User).filter(User.id == user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Prevent admin from demoting themselves
    if user.id == current_admin.id and user_data.is_admin is False:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot remove your own admin privileges"
        )

    # Update fields if provided
    if user_data.email is not None:
        # Check if email is already taken by another user
        existing = db.query(User).filter(
            User.email == user_data.email,
            User.id != user_id
        ).first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already in use"
            )
        user.email = user_data.email

    if user_data.username is not None:
        # Check if username is already taken by another user
        existing = db.query(User).filter(
            User.username == user_data.username,
            User.id != user_id
        ).first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Username already taken"
            )
        user.username = user_data.username

    if user_data.is_active is not None:
        user.is_active = user_data.is_active

    if user_data.is_verified is not None:
        user.is_verified = user_data.is_verified

    if user_data.is_admin is not None:
        user.is_admin = user_data.is_admin

    user.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(user)

    return user


@router.delete("/users/{user_id}", response_model=MessageResponse)
async def delete_user(
    user_id: int,
    current_admin: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """
    Delete a user and all associated data

    Admin only endpoint - PERMANENT deletion
    """
    user = db.query(User).filter(User.id == user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Prevent admin from deleting themselves
    if user.id == current_admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete your own account"
        )

    # Delete user's audio processing jobs and files
    jobs = db.query(AudioProcessingJob).filter(AudioProcessingJob.user_id == user_id).all()
    for job in jobs:
        # Delete physical files
        if job.input_file_path and os.path.exists(job.input_file_path):
            os.remove(job.input_file_path)
        if job.output_file_path and os.path.exists(job.output_file_path):
            os.remove(job.output_file_path)
        db.delete(job)

    # Delete user's usage statistics
    stats = db.query(UserUsageStats).filter(UserUsageStats.user_id == user_id).first()
    if stats:
        db.delete(stats)

    # Delete the user
    db.delete(user)
    db.commit()

    return {"message": f"User {user.username} and all associated data deleted successfully"}


@router.post("/users/{user_id}/password", response_model=MessageResponse)
async def update_user_password(
    user_id: int,
    password_data: AdminPasswordUpdate,
    current_admin: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """
    Update a user's password

    Admin only endpoint to change user passwords
    """
    user = db.query(User).filter(User.id == user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Hash new password
    user.hashed_password = get_password_hash(password_data.new_password)
    user.updated_at = datetime.utcnow()

    db.commit()

    return {"message": f"Password updated successfully for user {user.username}"}


@router.post("/users/{user_id}/verify", response_model=MessageResponse)
async def verify_user_email(
    user_id: int,
    current_admin: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """
    Manually verify a user's email

    Admin only endpoint to bypass email verification
    """
    user = db.query(User).filter(User.id == user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.is_verified = True
    user.verification_token = None
    user.verification_token_expires = None
    user.updated_at = datetime.utcnow()

    db.commit()

    return {"message": f"User {user.username} email verified successfully"}


@router.post("/users/{user_id}/activate", response_model=MessageResponse)
async def activate_user(
    user_id: int,
    current_admin: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """
    Activate a deactivated user

    Admin only endpoint
    """
    user = db.query(User).filter(User.id == user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.is_active = True
    user.updated_at = datetime.utcnow()

    db.commit()

    return {"message": f"User {user.username} activated successfully"}


@router.post("/users/{user_id}/deactivate", response_model=MessageResponse)
async def deactivate_user(
    user_id: int,
    current_admin: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """
    Deactivate a user (soft delete)

    Admin only endpoint to disable user access without deleting data
    """
    user = db.query(User).filter(User.id == user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Prevent admin from deactivating themselves
    if user.id == current_admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot deactivate your own account"
        )

    user.is_active = False
    user.updated_at = datetime.utcnow()

    db.commit()

    return {"message": f"User {user.username} deactivated successfully"}


# ==================== Guest Management ====================

@router.get("/guests")
async def list_guest_sessions(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    include_expired: bool = Query(False, description="Include expired sessions"),
    current_admin: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """
    List all guest sessions

    Admin only endpoint
    """
    query = db.query(GuestSession)

    if not include_expired:
        query = query.filter(GuestSession.expires_at > datetime.utcnow())

    query = query.order_by(desc(GuestSession.created_at))
    sessions = query.offset(skip).limit(limit).all()

    return sessions


@router.delete("/guests/{guest_id}", response_model=MessageResponse)
async def delete_guest_session(
    guest_id: str,
    current_admin: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """
    Delete a guest session and all associated data

    Admin only endpoint
    """
    session = db.query(GuestSession).filter(GuestSession.guest_id == guest_id).first()

    if not session:
        raise HTTPException(status_code=404, detail="Guest session not found")

    # Delete guest's audio processing jobs and files
    jobs = db.query(AudioProcessingJob).filter(AudioProcessingJob.guest_id == guest_id).all()
    for job in jobs:
        # Delete physical files
        if job.input_file_path and os.path.exists(job.input_file_path):
            os.remove(job.input_file_path)
        if job.output_file_path and os.path.exists(job.output_file_path):
            os.remove(job.output_file_path)
        db.delete(job)

    # Delete guest's usage statistics
    stats = db.query(UserUsageStats).filter(UserUsageStats.guest_id == guest_id).first()
    if stats:
        db.delete(stats)

    # Delete the guest session
    db.delete(session)
    db.commit()

    return {"message": f"Guest session {guest_id} and all associated data deleted successfully"}


# ==================== Job Management ====================

@router.get("/jobs")
async def list_all_jobs(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    status_filter: Optional[str] = Query(None, description="Filter by status: pending, processing, completed, failed"),
    user_id: Optional[int] = Query(None, description="Filter by user ID"),
    guest_id: Optional[str] = Query(None, description="Filter by guest ID"),
    current_admin: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """
    List all audio processing jobs

    Admin only endpoint with filtering options
    """
    query = db.query(AudioProcessingJob)

    if status_filter:
        query = query.filter(AudioProcessingJob.status == status_filter)

    if user_id:
        query = query.filter(AudioProcessingJob.user_id == user_id)

    if guest_id:
        query = query.filter(AudioProcessingJob.guest_id == guest_id)

    query = query.order_by(desc(AudioProcessingJob.created_at))
    jobs = query.offset(skip).limit(limit).all()

    return jobs


@router.delete("/jobs/{job_id}", response_model=MessageResponse)
async def delete_job(
    job_id: str,
    current_admin: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """
    Delete a specific job and its files

    Admin only endpoint
    """
    job = db.query(AudioProcessingJob).filter(AudioProcessingJob.job_id == job_id).first()

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Delete physical files
    if job.input_file_path and os.path.exists(job.input_file_path):
        os.remove(job.input_file_path)
    if job.output_file_path and os.path.exists(job.output_file_path):
        os.remove(job.output_file_path)

    db.delete(job)
    db.commit()

    return {"message": f"Job {job_id} deleted successfully"}


# ==================== System Statistics ====================

@router.get("/stats/overview")
async def get_system_overview(
    current_admin: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """
    Get comprehensive system statistics

    Admin only endpoint
    """
    # User statistics
    total_users = db.query(User).count()
    active_users = db.query(User).filter(User.is_active == True).count()
    verified_users = db.query(User).filter(User.is_verified == True).count()
    admin_users = db.query(User).filter(User.is_admin == True).count()

    # Guest statistics
    total_guests = db.query(GuestSession).count()
    active_guests = db.query(GuestSession).filter(
        GuestSession.expires_at > datetime.utcnow()
    ).count()

    # Job statistics
    total_jobs = db.query(AudioProcessingJob).count()
    pending_jobs = db.query(AudioProcessingJob).filter(AudioProcessingJob.status == "pending").count()
    processing_jobs = db.query(AudioProcessingJob).filter(AudioProcessingJob.status == "processing").count()
    completed_jobs = db.query(AudioProcessingJob).filter(AudioProcessingJob.status == "completed").count()
    failed_jobs = db.query(AudioProcessingJob).filter(AudioProcessingJob.status == "failed").count()

    # Calculate success rate
    finished_jobs = completed_jobs + failed_jobs
    success_rate = (completed_jobs / finished_jobs * 100) if finished_jobs > 0 else 0

    # Recent activity (last 24 hours)
    yesterday = datetime.utcnow() - timedelta(days=1)
    new_users_24h = db.query(User).filter(User.created_at >= yesterday).count()
    new_jobs_24h = db.query(AudioProcessingJob).filter(AudioProcessingJob.created_at >= yesterday).count()

    return {
        "users": {
            "total": total_users,
            "active": active_users,
            "verified": verified_users,
            "admins": admin_users,
            "inactive": total_users - active_users,
            "new_last_24h": new_users_24h
        },
        "guests": {
            "total": total_guests,
            "active": active_guests,
            "expired": total_guests - active_guests
        },
        "jobs": {
            "total": total_jobs,
            "pending": pending_jobs,
            "processing": processing_jobs,
            "completed": completed_jobs,
            "failed": failed_jobs,
            "success_rate_percent": round(success_rate, 2),
            "new_last_24h": new_jobs_24h
        },
        "timestamp": datetime.utcnow().isoformat()
    }


@router.post("/cleanup/expired", response_model=MessageResponse)
async def manual_cleanup_expired_files(
    current_admin: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """
    Manually trigger cleanup of expired files and sessions

    Admin only endpoint - normally runs automatically via Celery
    """
    deleted_jobs = 0
    deleted_guests = 0

    # Clean up expired jobs
    expired_jobs = db.query(AudioProcessingJob).filter(
        AudioProcessingJob.expires_at < datetime.utcnow()
    ).all()

    for job in expired_jobs:
        try:
            if job.input_file_path and os.path.exists(job.input_file_path):
                os.remove(job.input_file_path)
            if job.output_file_path and os.path.exists(job.output_file_path):
                os.remove(job.output_file_path)
            db.delete(job)
            deleted_jobs += 1
        except Exception as e:
            print(f"Error cleaning up job {job.job_id}: {str(e)}")

    # Clean up expired guest sessions
    expired_guests = db.query(GuestSession).filter(
        GuestSession.expires_at < datetime.utcnow()
    ).all()

    for guest in expired_guests:
        try:
            db.delete(guest)
            deleted_guests += 1
        except Exception as e:
            print(f"Error cleaning up guest {guest.guest_id}: {str(e)}")

    db.commit()

    return {
        "message": f"Cleanup completed: {deleted_jobs} jobs and {deleted_guests} guest sessions removed"
    }
