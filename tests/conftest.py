"""Test fixtures for Drive Service."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth import TokenData
from app.config import settings
from app.dependencies import get_drive_service
from app.main import app
from app.schemas import (
    DriveFileResponse,
    DriveFileUploadResponse,
    DriveFileLinkResponse,
    DriveFolderCreateResponse,
    DriveFolderResponse,
    DriveQuotaResponse,
    DriveSearchResponse,
    DriveStatsResponse,
)
from app.service import DriveService


# ============================================================================
# Service User (bypass auth)
# ============================================================================

_test_user = TokenData(id="test-user", user_name="test", mail="test@test.com", role="service")


# ============================================================================
# Mock Drive Service
# ============================================================================

def _make_mock_drive() -> AsyncMock:
    """Create a fully-mocked DriveService with sensible defaults."""
    mock = AsyncMock(spec=DriveService)

    mock.test_connection.return_value = True

    mock.search_files.return_value = DriveSearchResponse(
        files=[], total=0, next_page_token=None,
    )

    mock.get_file.return_value = DriveFileResponse(
        id="file-1", name="test.pdf", mime_type="application/pdf",
        size=1024, created_at=datetime.now(timezone.utc),
    )

    mock.get_file_link.return_value = DriveFileLinkResponse(
        id="file-1", name="test.pdf",
        view_link="https://drive.google.com/file/d/file-1/view",
        download_link="https://drive.google.com/uc?id=file-1",
    )

    mock.download_file_content.return_value = (
        b"PDF content", "test.pdf", "application/pdf",
    )

    mock.upload_file.return_value = DriveFileUploadResponse(
        id="new-file-1", name="uploaded.pdf", size=2048,
        view_link="https://drive.google.com/file/d/new-file-1/view",
        download_link="https://drive.google.com/uc?id=new-file-1",
    )

    mock.find_existing.return_value = None

    mock.create_folder.return_value = DriveFolderCreateResponse(
        id="folder-1", name="Test Folder",
        share_link="https://drive.google.com/drive/folders/folder-1",
    )

    mock.get_folder.return_value = DriveFolderResponse(
        id="folder-1", name="Test Folder", files_count=5,
    )

    mock.get_folder_link.return_value = "https://drive.google.com/drive/folders/folder-1"

    mock.get_quota.return_value = DriveQuotaResponse(
        total_bytes=16_106_127_360,
        used_bytes=1_073_741_824,
        available_bytes=15_032_385_536,
        usage_percent=6.67,
        auth_mode="Service Account (15 GB)",
    )

    mock.get_stats.return_value = DriveStatsResponse(
        total_files=42, total_folders=3,
    )

    mock.get_recent_files.return_value = DriveSearchResponse(
        files=[], total=0, next_page_token=None,
    )

    mock.rename.return_value = None
    mock.delete_file.return_value = None

    return mock


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def mock_drive_service() -> AsyncMock:
    """Provide a mock DriveService; override defaults per-test as needed."""
    return _make_mock_drive()


@pytest.fixture
def client(mock_drive_service: AsyncMock) -> TestClient:
    """Create a TestClient with auth and Drive service mocked out.

    - Auth is bypassed by overriding ``get_current_user``.
    - DriveService is replaced with the mock via ``get_drive_service``.
    - Lifespan is replaced with a no-op so Google credentials are never loaded.
    """
    from app.auth import get_current_user

    # No-op lifespan so we never touch real Google creds
    @asynccontextmanager
    async def _noop_lifespan(a: FastAPI) -> AsyncIterator[None]:
        a.state.http_client = httpx.AsyncClient()
        yield
        await a.state.http_client.aclose()

    original_lifespan = app.router.lifespan_context
    app.router.lifespan_context = _noop_lifespan

    app.dependency_overrides[get_current_user] = lambda: _test_user
    app.dependency_overrides[get_drive_service] = lambda: mock_drive_service

    with TestClient(app) as tc:
        yield tc

    # Clean up
    app.dependency_overrides.clear()
    app.router.lifespan_context = original_lifespan


@pytest.fixture
def unauth_client() -> TestClient:
    """Client WITHOUT auth override — for testing 401 responses.

    We still override get_drive_service because FastAPI resolves ALL
    dependencies concurrently, and the real one would fail without
    Google credentials.
    """
    @asynccontextmanager
    async def _noop_lifespan(a: FastAPI) -> AsyncIterator[None]:
        a.state.http_client = httpx.AsyncClient()
        yield
        await a.state.http_client.aclose()

    original_lifespan = app.router.lifespan_context
    app.router.lifespan_context = _noop_lifespan

    app.dependency_overrides.clear()
    # Still mock Drive so the dependency doesn't explode on missing state
    app.dependency_overrides[get_drive_service] = lambda: _make_mock_drive()

    with TestClient(app) as tc:
        yield tc

    app.router.lifespan_context = original_lifespan
