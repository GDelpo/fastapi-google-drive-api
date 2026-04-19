"""Tests for Drive API endpoints."""

import io
import pytest
from unittest.mock import AsyncMock

from app.schemas import DriveSearchResponse, DriveFileResponse


# ============================================================================
# Health
# ============================================================================


class TestHealth:
    """Test health check endpoint."""

    def test_health_returns_200(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] in ("healthy", "degraded")
        assert "version" in data
        assert "environment" in data
        assert "authMode" in data


# ============================================================================
# Files
# ============================================================================


class TestFiles:
    """Test /api/v1/files endpoints."""

    def test_search_files(self, client):
        response = client.get("/api/v1/files")
        assert response.status_code == 200
        data = response.json()
        assert "files" in data
        assert "total" in data

    def test_search_files_with_query(self, client, mock_drive_service):
        response = client.get("/api/v1/files", params={"query": "contrato"})
        assert response.status_code == 200
        mock_drive_service.search_files.assert_awaited_once()
        call_kwargs = mock_drive_service.search_files.call_args.kwargs
        assert call_kwargs["query"] == "contrato"

    def test_get_file(self, client):
        response = client.get("/api/v1/files/file-1")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "file-1"
        assert data["name"] == "test.pdf"

    def test_get_file_link(self, client):
        response = client.get("/api/v1/files/file-1/link")
        assert response.status_code == 200
        data = response.json()
        assert "viewLink" in data
        assert "downloadLink" in data

    def test_download_file(self, client):
        response = client.get("/api/v1/files/file-1/download")
        assert response.status_code == 200
        assert response.content == b"PDF content"
        assert response.headers["content-type"] == "application/pdf"

    def test_upload_file(self, client):
        file_content = io.BytesIO(b"fake PDF content")
        response = client.post(
            "/api/v1/files",
            files={"file": ("test.pdf", file_content, "application/pdf")},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["id"] == "new-file-1"

    def test_upload_file_skip_existing_found(self, client, mock_drive_service):
        """When skip_existing=true and file exists, return existing without uploading."""
        mock_drive_service.find_existing.return_value = DriveFileResponse(
            id="existing-1", name="test.pdf", mime_type="application/pdf",
            size=512,
            view_link="https://drive.google.com/file/d/existing-1/view",
            download_link="https://drive.google.com/uc?id=existing-1",
        )
        file_content = io.BytesIO(b"fake PDF content")
        response = client.post(
            "/api/v1/files",
            files={"file": ("test.pdf", file_content, "application/pdf")},
            data={"skip_existing": "true"},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["id"] == "existing-1"
        mock_drive_service.upload_file.assert_not_awaited()

    def test_upload_file_skip_existing_not_found(self, client, mock_drive_service):
        """When skip_existing=true but file doesn't exist, upload normally."""
        mock_drive_service.find_existing.return_value = None
        file_content = io.BytesIO(b"fake PDF content")
        response = client.post(
            "/api/v1/files",
            files={"file": ("test.pdf", file_content, "application/pdf")},
            data={"skip_existing": "true"},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["id"] == "new-file-1"
        mock_drive_service.find_existing.assert_awaited_once()
        mock_drive_service.upload_file.assert_awaited_once()

    def test_rename_file(self, client, mock_drive_service):
        response = client.patch(
            "/api/v1/files/file-1",
            json={"name": "nuevo_nombre.pdf"},
        )
        assert response.status_code == 200
        mock_drive_service.rename.assert_awaited_once_with("file-1", "nuevo_nombre.pdf")

    def test_delete_file(self, client, mock_drive_service):
        response = client.delete("/api/v1/files/file-1")
        assert response.status_code == 204
        mock_drive_service.delete_file.assert_awaited_once_with("file-1")


# ============================================================================
# Folders
# ============================================================================


class TestFolders:
    """Test /api/v1/folders endpoints."""

    def test_create_folder(self, client):
        response = client.post(
            "/api/v1/folders",
            json={"name": "Test Folder"},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["id"] == "folder-1"
        assert data["name"] == "Test Folder"

    def test_get_folder(self, client):
        response = client.get("/api/v1/folders/folder-1")
        assert response.status_code == 200
        data = response.json()
        assert data["filesCount"] == 5

    def test_get_folder_link(self, client):
        response = client.get("/api/v1/folders/folder-1/link")
        assert response.status_code == 200
        data = response.json()
        assert "share_link" in data

    def test_rename_folder(self, client, mock_drive_service):
        response = client.patch(
            "/api/v1/folders/folder-1",
            json={"name": "Renamed"},
        )
        assert response.status_code == 200
        mock_drive_service.rename.assert_awaited_once_with("folder-1", "Renamed")

    def test_delete_folder(self, client, mock_drive_service):
        response = client.delete("/api/v1/folders/folder-1")
        assert response.status_code == 204


# ============================================================================
# Quota & Stats
# ============================================================================


class TestQuotaAndStats:
    """Test quota, stats, and recent endpoints."""

    def test_get_quota(self, client):
        response = client.get("/api/v1/quota")
        assert response.status_code == 200
        data = response.json()
        assert "totalBytes" in data
        assert "usagePercent" in data
        assert "authMode" in data

    def test_get_stats(self, client):
        response = client.get("/api/v1/stats")
        assert response.status_code == 200
        data = response.json()
        assert data["totalFiles"] == 42
        assert data["totalFolders"] == 3

    def test_get_recent(self, client):
        response = client.get("/api/v1/recent")
        assert response.status_code == 200
        data = response.json()
        assert "files" in data


# ============================================================================
# Auth — unauthenticated requests
# ============================================================================


class TestAuth:
    """Test that endpoints require authentication."""

    def test_search_files_requires_auth(self, unauth_client):
        response = unauth_client.get("/api/v1/files")
        assert response.status_code == 401

    def test_get_file_requires_auth(self, unauth_client):
        response = unauth_client.get("/api/v1/files/some-id")
        assert response.status_code == 401

    def test_create_folder_requires_auth(self, unauth_client):
        response = unauth_client.post(
            "/api/v1/folders",
            json={"name": "Test Folder"},
        )
        assert response.status_code == 401

    def test_quota_requires_auth(self, unauth_client):
        response = unauth_client.get("/api/v1/quota")
        assert response.status_code == 401
