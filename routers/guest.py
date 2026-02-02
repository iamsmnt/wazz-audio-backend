"""Guest user routes for anonymous access"""

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session
from datetime import timedelta, datetime, timezone
import uuid

from wazz_shared.database import get_db
from wazz_shared.models import GuestSession
from wazz_shared.schemas import GuestTokenResponse, GuestSessionResponse
from auth import create_guest_token
from wazz_shared.config import get_shared_settings
from dependencies import get_current_user_or_guest

router = APIRouter(prefix="/guest", tags=["Guest"])
settings = get_shared_settings()


@router.post("/session", response_model=GuestTokenResponse)
def create_guest_session(request: Request, db: Session = Depends(get_db)):
    """
    Create a new guest session and return a guest token.
    No authentication required - anyone can call this.

    The guest token can be used for limited access to the API.
    """
    # Generate unique guest ID
    guest_id = str(uuid.uuid4())

    # Create guest token (expires in 7 days)
    guest_token, expires_at = create_guest_token(guest_id)

    # Get client info for tracking
    client_host = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")

    # Create guest session record
    guest_session = GuestSession(
        guest_id=guest_id,
        ip_address=client_host,
        user_agent=user_agent,
        expires_at=expires_at
    )

    db.add(guest_session)
    db.commit()
    db.refresh(guest_session)

    # Calculate expires_in seconds (from current time to expiration)
    expires_in = int((expires_at - datetime.utcnow()).total_seconds())

    return {
        "guest_token": guest_token,
        "guest_id": guest_id,
        "token_type": "bearer",
        "expires_in": expires_in
    }


@router.get("/session/{guest_id}", response_model=GuestSessionResponse)
def get_guest_session(guest_id: str, db: Session = Depends(get_db)):
    """
    Get information about a guest session.
    Useful for tracking guest activity.
    """
    guest_session = db.query(GuestSession).filter(GuestSession.guest_id == guest_id).first()

    if not guest_session:
        from fastapi import HTTPException, status
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Guest session not found"
        )

    return guest_session


@router.get("/example/mixed-access")
def mixed_access_example(
    current: dict = Depends(get_current_user_or_guest),
    db: Session = Depends(get_db)
):
    """
    Example endpoint demonstrating mixed authentication.
    Supports registered users, guest users, and anonymous access.

    This endpoint can be called:
    - Without any token (anonymous)
    - With a guest token (from POST /guest/session)
    - With a user access token (from POST /auth/login)
    """
    if current["type"] == "user":
        # Registered user
        user = current["user"]
        return {
            "message": f"Hello registered user {user.username}!",
            "user_type": "registered",
            "user_id": user.id,
            "email": user.email
        }

    elif current["type"] == "guest":
        # Guest user with session
        guest_id = current["guest_id"]
        guest_session = current.get("session")

        # Update last_active_at
        if guest_session:
            from sqlalchemy import update
            db.execute(
                update(GuestSession)
                .where(GuestSession.guest_id == guest_id)
                .values(last_active_at=datetime.utcnow())
            )
            db.commit()

        return {
            "message": f"Hello guest user!",
            "user_type": "guest",
            "guest_id": guest_id,
            "session_created": guest_session.created_at if guest_session else None
        }

    else:
        # Anonymous user (no token provided)
        return {
            "message": "Hello anonymous visitor!",
            "user_type": "anonymous",
            "note": "Create a guest session at POST /guest/session or register at POST /auth/signup"
        }
