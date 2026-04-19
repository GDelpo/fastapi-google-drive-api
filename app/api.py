"""API endpoints for Drive Service."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status

from app.dependencies import DriveServiceDep, ServiceUser
from app.schemas import (
    BatchUploadResponse,
    DriveFileLinkResponse,
    DriveFileResponse,
    DriveFolderCreateRequest,
    DriveFolderCreateResponse,
    DriveFolderResponse,
    DriveQuotaResponse,
    DriveSearchResponse,
    DriveFileUploadResponse,
    DriveStatsResponse,
    FolderUploadResponse,
    RenameRequest,
)

router = APIRouter()


# ============================================================================
# File Endpoints
# ============================================================================


@router.get("/files", response_model=DriveSearchResponse)
async def search_files(
    drive: DriveServiceDep,
    current_user: ServiceUser,
    query: str | None = Query(None, description="Search query for file name"),
    folder_id: str | None = Query(None, description="Filter by parent folder ID"),
    mime_type: str | None = Query(None, description="Filter by MIME type"),
    created_after: datetime | None = Query(None, description="Filter files created after this date"),
    page_size: int = Query(100, ge=1, le=1000, description="Number of results per page"),
    page_token: str | None = Query(None, description="Token for pagination"),
    include_folders: bool = Query(False, description="Include folders in results"),
):
    """Search files in Google Drive.
    
    Supports filtering by query, folder, MIME type, and creation date.
    Results are paginated. Folders are excluded by default.
    """
    return await drive.search_files(
        query=query,
        folder_id=folder_id,
        mime_type=mime_type,
        created_after=created_after,
        page_size=page_size,
        page_token=page_token,
        exclude_folders=not include_folders,
    )


@router.get("/files/{file_id}", response_model=DriveFileResponse)
async def get_file(
    file_id: str,
    drive: DriveServiceDep,
    current_user: ServiceUser,
):
    """Get file metadata by ID."""
    return await drive.get_file(file_id)


@router.get("/files/{file_id}/link", response_model=DriveFileLinkResponse)
async def get_file_link(
    file_id: str,
    drive: DriveServiceDep,
    current_user: ServiceUser,
):
    """Get view and download links for a file."""
    return await drive.get_file_link(file_id)


@router.get("/files/{file_id}/download")
async def download_file(
    file_id: str,
    drive: DriveServiceDep,
    current_user: ServiceUser,
):
    """Download file content from Google Drive."""
    from fastapi.responses import Response
    
    content, filename, mime_type = await drive.download_file_content(file_id)
    
    return Response(
        content=content,
        media_type=mime_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(content)),
        },
    )


@router.post(
    "/files",
    response_model=DriveFileUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_file(
    drive: DriveServiceDep,
    current_user: ServiceUser,
    file: UploadFile = File(..., description="File to upload"),
    folder_id: str | None = Form(None, description="Parent folder ID"),
    custom_name: str | None = Form(None, description="Custom filename (optional)"),
    skip_existing: bool = Form(False, description="Return existing file instead of uploading a duplicate"),
):
    """Upload a file to Google Drive.
    
    The file will be uploaded to the specified folder or the default root folder.
    Files inherit the sharing permissions of their parent folder.
    When skip_existing=true, checks for an existing file with the same name
    in the target folder and returns it instead of uploading again.
    """
    filename = custom_name or file.filename or "unnamed_file"

    if skip_existing:
        existing = await drive.find_existing(filename, folder_id)
        if existing:
            return DriveFileUploadResponse(
                id=existing.id,
                name=existing.name,
                size=existing.size,
                view_link=existing.view_link or f"https://drive.google.com/file/d/{existing.id}/view",
                download_link=existing.download_link or f"https://drive.google.com/uc?export=download&id={existing.id}",
            )

    return await drive.upload_file(
        file_content=file.file,
        filename=filename,
        folder_id=folder_id,
        mime_type=file.content_type,
    )


@router.post(
    "/files/batch",
    response_model=BatchUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_files_batch(
    drive: DriveServiceDep,
    current_user: ServiceUser,
    files: list[UploadFile] = File(..., description="Files to upload"),
    folder_id: str | None = Form(None, description="Parent folder ID"),
    skip_existing: bool = Form(False, description="Skip files that already exist (match by exact name in folder)"),
):
    """Upload multiple files to Google Drive.
    
    All files will be uploaded to the same folder.
    Files inherit the sharing permissions of their parent folder.
    When skip_existing=true, files already present in the folder are
    reused instead of uploaded again (deduplication by name).
    Returns a summary with success/failure for each file.
    """
    file_tuples = [(f.file, f.filename or f"file_{i}") for i, f in enumerate(files)]
    return await drive.upload_batch(file_tuples, folder_id, skip_existing=skip_existing)


@router.delete("/files/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_file(
    file_id: str,
    drive: DriveServiceDep,
    current_user: ServiceUser,
):
    """Delete a file from Google Drive."""
    await drive.delete_file(file_id)


@router.patch("/files/{file_id}", response_model=DriveFileResponse)
async def rename_file(
    file_id: str,
    body: RenameRequest,
    drive: DriveServiceDep,
    current_user: ServiceUser,
):
    """Rename a file in Google Drive."""
    await drive.rename(file_id, body.name)
    return await drive.get_file(file_id)


# ============================================================================
# Folder Endpoints
# ============================================================================


@router.post(
    "/folders",
    response_model=DriveFolderCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_folder(
    request: DriveFolderCreateRequest,
    drive: DriveServiceDep,
    current_user: ServiceUser,
):
    """Create a new folder in Google Drive.
    
    The folder will be created in the specified parent folder or the default root.
    By default, a shareable link is generated for the folder.
    """
    return await drive.create_folder(
        name=request.name,
        parent_folder_id=request.parent_folder_id,
        share=True,
    )


@router.get("/folders/{folder_id}", response_model=DriveFolderResponse)
async def get_folder(
    folder_id: str,
    drive: DriveServiceDep,
    current_user: ServiceUser,
):
    """Get folder information including file count."""
    return await drive.get_folder(folder_id)


@router.get("/folders/{folder_id}/link")
async def get_folder_link(
    folder_id: str,
    drive: DriveServiceDep,
    current_user: ServiceUser,
):
    """Get a shareable link for a folder.
    
    Creates a public "anyone with link" permission if not already shared.
    """
    link = await drive.get_folder_link(folder_id)
    return {"folder_id": folder_id, "share_link": link}


@router.post(
    "/folders/upload",
    response_model=FolderUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_folder(
    drive: DriveServiceDep,
    current_user: ServiceUser,
    files: list[UploadFile] = File(..., description="Files to upload as folder contents"),
    folder_name: str = Form(..., description="Name for the new folder"),
    parent_folder_id: str | None = Form(None, description="Parent folder ID"),
):
    """Upload multiple files into a new folder.
    
    Creates a new folder and uploads all files into it.
    Returns a shareable link to the folder along with upload results.
    """
    # Create folder first
    folder = await drive.create_folder(
        name=folder_name,
        parent_folder_id=parent_folder_id,
        share=True,
    )
    
    # Upload files to the new folder
    file_tuples = [(f.file, f.filename or f"file_{i}") for i, f in enumerate(files)]
    result = await drive.upload_batch(file_tuples, folder.id)
    
    return FolderUploadResponse(
        **result.model_dump(),
        folder_id=folder.id,
        folder_link=folder.share_link,
    )


# ============================================================================
# Quota Endpoint
# ============================================================================


@router.get("/quota", response_model=DriveQuotaResponse)
async def get_quota(
    drive: DriveServiceDep,
    current_user: ServiceUser,
):
    """Get Google Drive storage quota information."""
    return await drive.get_quota()


# ============================================================================
# Delete / Rename Folder
# ============================================================================


@router.delete("/folders/{folder_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_folder(
    folder_id: str,
    drive: DriveServiceDep,
    current_user: ServiceUser,
):
    """Delete a folder from Google Drive (and all its contents)."""
    await drive.delete_file(folder_id)


@router.patch("/folders/{folder_id}", response_model=DriveFolderResponse)
async def rename_folder(
    folder_id: str,
    body: RenameRequest,
    drive: DriveServiceDep,
    current_user: ServiceUser,
):
    """Rename a folder in Google Drive."""
    await drive.rename(folder_id, body.name)
    return await drive.get_folder(folder_id)


# ============================================================================
# Stats Endpoint
# ============================================================================


@router.get("/stats", response_model=DriveStatsResponse)
async def get_stats(
    drive: DriveServiceDep,
    current_user: ServiceUser,
):
    """Get Drive usage statistics (file/folder counts). Results are cached 60s."""
    return await drive.get_stats()


@router.get("/recent", response_model=DriveSearchResponse)
async def get_recent_files(
    drive: DriveServiceDep,
    current_user: ServiceUser,
    limit: int = 10,
):
    """Get recent files (cached 30s). Optimized for dashboard overview."""
    return await drive.get_recent_files(limit=min(limit, 50))
