"""Request/Response schemas for the Drive Service."""

from datetime import datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field


def to_camel(string: str) -> str:
    """Convert snake_case to camelCase."""
    components = string.split("_")
    return components[0] + "".join(x.title() for x in components[1:])


class CamelModel(BaseModel):
    """Base model with camelCase serialization."""

    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
        alias_generator=to_camel,
    )


# ============================================================================
# Generic Response Models
# ============================================================================

T = TypeVar("T")


class PaginatedResponse(CamelModel, Generic[T]):
    """Generic paginated response."""

    items: list[T]
    total: int
    page: int
    page_size: int
    has_more: bool


class ErrorResponse(BaseModel):
    """Error response model."""

    error: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


# ============================================================================
# Drive File Schemas
# ============================================================================


class DriveFileResponse(CamelModel):
    """Response schema for a Drive file."""

    id: str
    name: str
    mime_type: str | None = None
    size: int | None = None
    created_at: datetime | None = None
    modified_at: datetime | None = None
    view_link: str | None = None
    download_link: str | None = None
    web_content_link: str | None = None


class DriveFileUploadResponse(CamelModel):
    """Response after uploading a file."""

    id: str
    name: str
    size: int | None = None
    view_link: str
    download_link: str


class DriveFileLinkResponse(CamelModel):
    """Response with file links."""

    id: str
    name: str
    view_link: str
    download_link: str


# ============================================================================
# Drive Folder Schemas
# ============================================================================


class DriveFolderResponse(CamelModel):
    """Response schema for a Drive folder."""

    id: str
    name: str
    created_at: datetime | None = None
    share_link: str | None = None
    files_count: int = 0


class DriveFolderCreateRequest(CamelModel):
    """Request to create a folder."""

    name: str
    parent_folder_id: str | None = None


class DriveFolderCreateResponse(CamelModel):
    """Response after creating a folder."""

    id: str
    name: str
    share_link: str | None = None


# ============================================================================
# Search Schemas
# ============================================================================


class DriveSearchResponse(CamelModel):
    """Response from a Drive search."""

    files: list[DriveFileResponse]
    total: int
    next_page_token: str | None = None


# ============================================================================
# Quota Schemas
# ============================================================================


class DriveQuotaResponse(CamelModel):
    """Response with Drive quota information."""

    total_bytes: int
    used_bytes: int
    available_bytes: int
    usage_percent: float
    auth_mode: str = "unknown"


# ============================================================================
# Batch Upload Schemas
# ============================================================================


class BatchUploadItem(CamelModel):
    """Single item in a batch upload result."""

    filename: str
    success: bool
    file_id: str | None = None
    view_link: str | None = None
    error: str | None = None
    existed: bool = False


class BatchUploadResponse(CamelModel):
    """Response from a batch upload."""

    total: int
    successful: int
    failed: int
    items: list[BatchUploadItem]


class FolderUploadResponse(BatchUploadResponse):
    """Response from uploading files into a newly created folder."""

    folder_id: str
    folder_link: str | None = None


# ============================================================================
# Health Check Schemas
# ============================================================================


class DependencyStatus(CamelModel):
    """Health status of a single dependency."""

    status: str  # healthy | degraded | unhealthy
    latency_ms: float | None = None
    error: str | None = None


class HealthResponse(CamelModel):
    """Health check response."""

    status: str
    version: str
    environment: str
    auth_mode: str = "unknown"
    dependencies: dict[str, DependencyStatus] = Field(default_factory=dict)


# ============================================================================
# Stats Schemas
# ============================================================================


class DriveStatsResponse(CamelModel):
    """Drive usage statistics."""

    total_files: int = 0
    total_folders: int = 0


# ============================================================================
# Rename Schema
# ============================================================================


class RenameRequest(CamelModel):
    """Request to rename a file or folder."""

    name: str
