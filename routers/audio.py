"""Audio processing endpoints"""

from fastapi import APIRouter, UploadFile, File, Form, Depends, HTTPException, status, Request, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from sqlalchemy import desc
from typing import Optional, List
from datetime import datetime, timedelta, timezone
import os
import uuid
import shutil
import mimetypes

from wazz_shared.database import get_db
from wazz_shared.models import AudioProcessingJob, User
from wazz_shared.schemas import AudioUploadResponse, AudioJobStatusResponse, ProjectResponse, ProjectRenameRequest, MessageResponse
from dependencies import get_optional_current_user
from wazz_shared.config import get_shared_settings
from wazz_shared.usage_tracking import track_file_upload, track_file_download
from celery_init import celery_app

router = APIRouter(prefix="/audio", tags=["audio"])
settings = get_shared_settings()


def get_audio_metadata(file_path: str) -> dict:
    """Extract audio metadata using a library like soundfile or wave"""
    try:
        import wave
        with wave.open(file_path, 'rb') as audio:
            return {
                "sample_rate": audio.getframerate(),
                "channels": audio.getnchannels(),
                "duration": audio.getnframes() / audio.getframerate() if audio.getframerate() > 0 else None
            }
    except Exception:
        # If wave doesn't work, try other formats or return None
        return {"sample_rate": None, "channels": None, "duration": None}


@router.post("/upload", response_model=AudioUploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_audio(
    file: UploadFile = File(...),
    processing_type: Optional[str] = Form(None),
    request: Request = None,
    current_user: Optional[User] = Depends(get_optional_current_user),
    db: Session = Depends(get_db)
):
    """
    Upload an audio file for processing

    - Accepts audio files up to configured size limit
    - Supports both authenticated users and guest sessions
    - Returns job_id for tracking processing status
    - Files are queued and will be processed when workers are available
    """

    # Validate file format
    file_extension = os.path.splitext(file.filename)[1].lower()
    if file_extension not in settings.allowed_audio_formats:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file format. Allowed formats: {', '.join(settings.allowed_audio_formats)}"
        )

    # Create upload directory if it doesn't exist
    os.makedirs(settings.upload_dir, exist_ok=True)

    # Generate unique filename
    unique_filename = f"{uuid.uuid4()}{file_extension}"
    file_path = os.path.join(settings.upload_dir, unique_filename)

    # Convert to absolute path so worker can access it
    absolute_file_path = os.path.abspath(file_path)

    # Save file to disk
    try:
        with open(absolute_file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save file: {str(e)}"
        )

    # Get file size
    file_size = os.path.getsize(absolute_file_path)

    # Check file size limit
    max_size_bytes = settings.max_file_size_mb * 1024 * 1024
    if file_size > max_size_bytes:
        os.remove(absolute_file_path)  # Clean up
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File too large. Maximum size: {settings.max_file_size_mb}MB"
        )

    # Extract audio metadata
    metadata = get_audio_metadata(absolute_file_path)

    # Determine user/guest info
    user_id = current_user.id if current_user else None
    guest_id = None

    if not current_user:
        # Try to get guest_id from headers or create a new one
        guest_id = request.headers.get("X-Guest-ID") if request else str(uuid.uuid4())

    # Calculate expiry time
    expires_at = datetime.now(timezone.utc) + timedelta(hours=settings.file_expiry_hours)

    # Create job record in database
    job = AudioProcessingJob(
        filename=unique_filename,
        original_filename=file.filename,
        file_size=file_size,
        file_format=file_extension.replace(".", ""),
        duration=metadata.get("duration"),
        sample_rate=metadata.get("sample_rate"),
        channels=metadata.get("channels"),
        input_file_path=absolute_file_path,  # Use absolute path so worker can find it
        user_id=user_id,
        guest_id=guest_id,
        status="pending",
        progress=0.0,
        processing_type=processing_type,
        expires_at=expires_at
    )

    db.add(job)
    db.commit()
    db.refresh(job)

    # Queue Celery task for processing
    try:
        task = celery_app.send_task('tasks.process_audio_task', args=[job.job_id], queue='audio_processing')
        job.job_metadata = {"celery_task_id": task.id}
        db.commit()

        # Track file upload for usage statistics
        track_file_upload(
            db=db,
            user_id=job.user_id,
            guest_id=job.guest_id,
            file_size=float(job.file_size),
            processing_type=processing_type or "speech_enhancement"
        )
    except Exception as e:
        # If task queueing fails, mark job as failed
        job.status = "failed"
        job.error_message = f"Failed to queue processing task: {str(e)}"
        db.commit()

    return AudioUploadResponse(
        job_id=job.job_id,
        status=job.status,
        filename=job.filename,
        original_filename=job.original_filename,
        file_size=job.file_size,
        file_format=job.file_format,
        duration=job.duration,
        sample_rate=job.sample_rate,
        channels=job.channels,
        user_id=job.user_id,
        guest_id=job.guest_id,
        created_at=job.created_at,
        expires_at=job.expires_at,
        message="File uploaded successfully. Processing will begin shortly."
    )


@router.get("/projects", response_model=List[ProjectResponse])
async def list_projects(
    request: Request,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    status_filter: Optional[str] = Query(None, alias="status"),
    current_user: Optional[User] = Depends(get_optional_current_user),
    db: Session = Depends(get_db),
):
    """
    List audio processing jobs for the current user or guest.

    Returns projects ordered by most recent first.
    Supports pagination and optional status filtering.
    """
    user_id = current_user.id if current_user else None
    guest_id = request.headers.get("X-Guest-ID") if not current_user else None

    if not user_id and not guest_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    query = db.query(AudioProcessingJob)

    if user_id:
        query = query.filter(AudioProcessingJob.user_id == user_id)
    else:
        query = query.filter(AudioProcessingJob.guest_id == guest_id)

    if status_filter:
        query = query.filter(AudioProcessingJob.status == status_filter)

    query = query.order_by(desc(AudioProcessingJob.created_at))
    return query.offset(skip).limit(limit).all()


@router.patch("/projects/{job_id}", response_model=ProjectResponse)
async def rename_project(
    job_id: str,
    body: ProjectRenameRequest,
    request: Request,
    current_user: Optional[User] = Depends(get_optional_current_user),
    db: Session = Depends(get_db),
):
    """Rename a project. User/guest must own the job."""
    job = db.query(AudioProcessingJob).filter(
        AudioProcessingJob.job_id == job_id
    ).first()

    if not job:
        raise HTTPException(status_code=404, detail="Project not found")

    if current_user:
        if job.user_id != current_user.id:
            raise HTTPException(status_code=403, detail="Not authorized")
    else:
        guest_id = request.headers.get("X-Guest-ID") if request else None
        if not guest_id or job.guest_id != guest_id:
            raise HTTPException(status_code=403, detail="Not authorized")

    job.project_name = body.project_name
    db.commit()
    db.refresh(job)
    return job


@router.delete("/projects/{job_id}", response_model=MessageResponse)
async def delete_project(
    job_id: str,
    request: Request,
    current_user: Optional[User] = Depends(get_optional_current_user),
    db: Session = Depends(get_db),
):
    """Delete a project and its files. User/guest must own the job."""
    job = db.query(AudioProcessingJob).filter(
        AudioProcessingJob.job_id == job_id
    ).first()

    if not job:
        raise HTTPException(status_code=404, detail="Project not found")

    if current_user:
        if job.user_id != current_user.id:
            raise HTTPException(status_code=403, detail="Not authorized")
    else:
        guest_id = request.headers.get("X-Guest-ID") if request else None
        if not guest_id or job.guest_id != guest_id:
            raise HTTPException(status_code=403, detail="Not authorized")

    # Delete physical files
    if job.input_file_path and os.path.exists(job.input_file_path):
        os.remove(job.input_file_path)
    if job.output_file_path and os.path.exists(job.output_file_path):
        os.remove(job.output_file_path)

    db.delete(job)
    db.commit()

    return {"message": f"Project {job_id} deleted"}


@router.get("/status/{job_id}", response_model=AudioJobStatusResponse)
async def get_job_status(
    job_id: str,
    request: Request = None,
    current_user: Optional[User] = Depends(get_optional_current_user),
    db: Session = Depends(get_db)
):
    """
    Get processing status for a job

    - Returns current status, progress, and error information
    - Authorization check: user/guest must own the job
    """
    # Fetch job
    job = db.query(AudioProcessingJob).filter(
        AudioProcessingJob.job_id == job_id
    ).first()

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Authorization: Check user owns this job
    if current_user:
        if job.user_id != current_user.id:
            raise HTTPException(status_code=403, detail="Not authorized to access this job")
    else:
        # For guests, check guest_id from headers
        guest_id = request.headers.get("X-Guest-ID") if request else None
        if not guest_id or job.guest_id != guest_id:
            raise HTTPException(status_code=403, detail="Not authorized to access this job")

    return AudioJobStatusResponse(
        job_id=job.job_id,
        status=job.status,
        progress=job.progress,
        filename=job.filename,
        original_filename=job.original_filename,
        processing_type=job.processing_type,
        error_message=job.error_message,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        output_available=(job.status == "completed" and job.output_file_path is not None)
    )


@router.get("/download/{job_id}")
async def download_processed_audio(
    job_id: str,
    request: Request = None,
    current_user: Optional[User] = Depends(get_optional_current_user),
    db: Session = Depends(get_db)
):
    """
    Download processed audio file

    - Requires job to be in 'completed' status
    - Authorization check: user/guest must own the job
    - Streams file for efficient download
    """
    # Fetch job
    job = db.query(AudioProcessingJob).filter(
        AudioProcessingJob.job_id == job_id
    ).first()

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Authorization check
    if current_user:
        if job.user_id != current_user.id:
            raise HTTPException(status_code=403, detail="Not authorized to access this job")
    else:
        guest_id = request.headers.get("X-Guest-ID") if request else None
        if not guest_id or job.guest_id != guest_id:
            raise HTTPException(status_code=403, detail="Not authorized to access this job")

    # Status validation
    if job.status != "completed":
        raise HTTPException(
            status_code=400,
            detail=f"Job is not completed yet. Current status: {job.status}"
        )

    if not job.output_file_path:
        raise HTTPException(status_code=500, detail="Output file path not set")

    # File existence check
    if not os.path.exists(job.output_file_path):
        raise HTTPException(status_code=500, detail="Processed file not found on server")

    # Determine content type
    content_type, _ = mimetypes.guess_type(job.output_file_path)
    if content_type is None:
        content_type = "audio/wav"  # Default for audio files

    # Track file download for usage statistics
    track_file_download(
        db=db,
        user_id=job.user_id,
        guest_id=job.guest_id
    )

    # Generate download filename: {original_name}_updated.{extension}
    filename_without_ext, file_ext = os.path.splitext(job.original_filename)
    download_filename = f"{filename_without_ext}_updated{file_ext}"

    # Return file response with streaming
    return FileResponse(
        path=job.output_file_path,
        media_type=content_type,
        filename=download_filename,
        headers={
            "Content-Disposition": f'attachment; filename="{download_filename}"'
        }
    )
