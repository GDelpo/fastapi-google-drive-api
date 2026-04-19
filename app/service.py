"""Google Drive Service - Core business logic."""

import asyncio
import mimetypes
import random
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import BinaryIO

import httplib2
import google_auth_httplib2
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials as OAuth2Credentials
from googleapiclient.discovery import Resource, build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload, MediaIoBaseUpload

from app.config import settings
from app.exceptions import DriveAPIError, EntityNotFoundError, FileUploadError, QuotaExceededError
from app.logger import get_logger
from app.schemas import (
    BatchUploadItem,
    BatchUploadResponse,
    DriveFileResponse,
    DriveFileLinkResponse,
    DriveFileUploadResponse,
    DriveFolderCreateResponse,
    DriveFolderResponse,
    DriveQuotaResponse,
    DriveSearchResponse,
    DriveStatsResponse,
)

logger = get_logger(__name__)

# Constants
DRIVE_API_PAGE_SIZE = 100
FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"
# Connection timeout for Google API calls (seconds)
_HTTP_TIMEOUT = 30


def _build_authed_http(credentials):
    """Build an authorized httplib2.Http with explicit timeout.

    Each call creates a fresh Http so concurrent threads never share
    the same socket/connection state (httplib2 is NOT thread-safe).
    """
    base_http = httplib2.Http(timeout=_HTTP_TIMEOUT)
    return google_auth_httplib2.AuthorizedHttp(credentials, http=base_http)


class DriveService:
    """Service for Google Drive operations.

    Uses a per-call httplib2.Http to avoid thread-safety issues when
    executing Google API requests via ``asyncio.to_thread``.
    """

    def __init__(self):
        """Initialize Drive service."""
        self._service: Resource | None = None
        self._credentials: service_account.Credentials | OAuth2Credentials | None = None
        self._auth_mode: str = "not_initialized"
        # Lock to serialise GoogleAPI calls that share the underlying Resource
        # object (httplib2 within google-api-python-client is NOT thread-safe).
        self._lock = asyncio.Lock()

    @property
    def auth_mode(self) -> str:
        """Return the active authentication mode label."""
        # Trigger lazy init so _auth_mode is set
        _ = self.service
        return self._auth_mode

    @property
    def service(self) -> Resource:
        """Lazy initialization of Drive API service."""
        if self._service is None:
            self._service = self._create_service()
        return self._service

    def _create_service(self) -> Resource:
        """Create and authenticate Google Drive service.

        Supports two authentication modes:
        - **OAuth2** (personal account): uses refresh_token to act as the
          account owner.  Files consume the owner's quota (e.g. 2 TB).
          Preferred when GOOGLE_OAUTH_* env vars are set.
        - **Service Account**: uses a JSON key file.  Files are owned by
          the service account and consume its own 15 GB quota.
        """
        try:
            if settings.use_oauth:
                self._credentials = OAuth2Credentials(
                    token=None,
                    refresh_token=settings.google_oauth_refresh_token,
                    client_id=settings.google_oauth_client_id,
                    client_secret=settings.google_oauth_client_secret.get_secret_value(),
                    token_uri="https://oauth2.googleapis.com/token",
                    scopes=settings.google_scopes,
                )
                self._auth_mode = "OAuth2 (cuenta personal)"
                auth_mode = self._auth_mode
            else:
                credentials_path = Path(settings.google_credentials_path)
                if not credentials_path.exists():
                    raise DriveAPIError(
                        f"Credentials file not found at {credentials_path}",
                        operation="init",
                    )
                self._credentials = service_account.Credentials.from_service_account_file(
                    str(credentials_path),
                    scopes=settings.google_scopes,
                )
                self._auth_mode = "Service Account (15 GB)"
                auth_mode = self._auth_mode

            service = build(
                "drive", "v3",
                credentials=self._credentials,
                cache_discovery=False,
            )
            logger.info("✅ Drive service initialized (%s)", auth_mode)
            return service

        except FileNotFoundError as error:
            logger.error("Credentials file not found: %s", error)
            raise DriveAPIError(f"Drive credentials not found: {error}", operation="init") from error
        except Exception as error:
            logger.error("Failed to create Drive service: %s", error)
            raise DriveAPIError(f"Failed to initialize Drive service: {error}", operation="init") from error

    async def _exec(self, request, *, _retries: int = 3):
        """Execute a Google API request with retry, timeout, and thread-safety.

        Each call gets its own ``httplib2.Http`` with an explicit timeout
        so that a slow/broken connection never blocks for 60+ seconds.
        The asyncio lock ensures only one request-at-a-time touches the
        underlying Resource object (httplib2 is not thread-safe).
        """
        last_error = None
        for attempt in range(1, _retries + 1):
            try:
                async with self._lock:
                    # Inject a fresh authorized Http with timeout for THIS call
                    req = request
                    creds = self._credentials
                    result = await asyncio.to_thread(
                        lambda: req.execute(http=_build_authed_http(creds))
                    )
                return result
            except HttpError as exc:
                status = exc.resp.status if exc.resp else 0
                if status in (429, 500, 502, 503) and attempt < _retries:
                    wait = min(2 ** attempt + random.random(), 8)
                    logger.warning(
                        "Google API transient error %s (attempt %d/%d), retrying in %.1fs",
                        status, attempt, _retries, wait,
                    )
                    last_error = exc
                    await asyncio.sleep(wait)
                    continue
                raise
            except Exception as exc:
                if attempt < _retries:
                    wait = min(2 ** attempt + random.random(), 8)
                    logger.warning(
                        "Google API error '%s' (attempt %d/%d), retrying in %.1fs",
                        exc, attempt, _retries, wait,
                    )
                    last_error = exc
                    await asyncio.sleep(wait)
                    continue
                raise
        raise last_error  # type: ignore[misc]

    async def test_connection(self) -> bool:
        """Test if Drive connection is working."""
        try:
            await self._exec(self.service.about().get(fields="user"))
            return True
        except Exception as e:
            logger.warning("Drive connection test failed: %s", e)
            return False

    # =========================================================================
    # File Operations
    # =========================================================================

    async def get_file(self, file_id: str) -> DriveFileResponse:
        """Get file metadata by ID."""
        try:
            file = await self._exec(self.service.files().get(
                fileId=file_id,
                fields="id, name, mimeType, size, createdTime, modifiedTime, webViewLink, webContentLink",
            ))

            return DriveFileResponse(
                id=file["id"],
                name=file["name"],
                mime_type=file.get("mimeType"),
                size=int(file["size"]) if file.get("size") else None,
                created_at=file.get("createdTime"),
                modified_at=file.get("modifiedTime"),
                view_link=file.get("webViewLink"),
                web_content_link=file.get("webContentLink"),
                download_link=f"https://drive.google.com/uc?export=download&id={file['id']}",
            )
        except HttpError as error:
            if error.resp.status == 404:
                raise EntityNotFoundError("File", file_id) from error
            raise DriveAPIError(str(error), operation="get_file") from error

    async def get_file_link(self, file_id: str) -> DriveFileLinkResponse:
        """Get view and download links for a file."""
        try:
            file = await self._exec(self.service.files().get(
                fileId=file_id,
                fields="id, name, webViewLink",
            ))

            return DriveFileLinkResponse(
                id=file["id"],
                name=file["name"],
                view_link=file.get("webViewLink", f"https://drive.google.com/file/d/{file_id}/view"),
                download_link=f"https://drive.google.com/uc?export=download&id={file_id}",
            )
        except HttpError as error:
            if error.resp.status == 404:
                raise EntityNotFoundError("File", file_id) from error
            raise DriveAPIError(str(error), operation="get_file_link") from error

    async def download_file_content(self, file_id: str) -> tuple[bytes, str, str]:
        """Download file content from Google Drive.
        
        Returns tuple of (content_bytes, filename, mime_type).
        """
        try:
            # Get file metadata first for filename
            file = await self._exec(self.service.files().get(
                fileId=file_id,
                fields="id, name, mimeType",
            ))
            
            filename = file.get("name", f"{file_id}.bin")
            mime_type = file.get("mimeType", "application/octet-stream")
            
            # Download file content in a thread (sync MediaIoBaseDownload)
            def _download_sync(creds):
                http = _build_authed_http(creds)
                request = self.service.files().get_media(fileId=file_id)
                buffer = BytesIO()
                downloader = MediaIoBaseDownload(buffer, request)
                done = False
                while not done:
                    status, done = downloader.next_chunk(http=http)
                return buffer.getvalue()

            async with self._lock:
                content = await asyncio.to_thread(_download_sync, self._credentials)
            logger.info("Downloaded file %s: %s (%s bytes)", file_id, filename, len(content))
            return content, filename, mime_type
            
        except HttpError as error:
            if error.resp.status == 404:
                raise EntityNotFoundError("File", file_id) from error
            raise DriveAPIError(str(error), operation="download_file") from error

    async def search_files(
        self,
        query: str | None = None,
        folder_id: str | None = None,
        mime_type: str | None = None,
        created_after: datetime | None = None,
        page_size: int = 100,
        page_token: str | None = None,
        exclude_folders: bool = True,
    ) -> DriveSearchResponse:
        """Search files in Drive.

        When no folder_id is specified and a root folder is configured,
        results are scoped to the root folder (direct children).
        By default, folders are excluded (only actual files are returned).
        """
        try:
            # Build query
            query_parts = ["trashed=false"]

            # Exclude folders by default
            if exclude_folders and not mime_type:
                query_parts.append(f"mimeType!='{FOLDER_MIME_TYPE}'")

            # Scope to folder: explicit > root default
            effective_folder = folder_id or settings.google_root_folder_id
            if effective_folder:
                query_parts.append(f"'{effective_folder}' in parents")

            if mime_type:
                query_parts.append(f"mimeType='{mime_type}'")

            if created_after:
                query_parts.append(f"createdTime >= '{created_after.isoformat()}'")

            if query:
                query_parts.append(f"name contains '{query}'")

            full_query = " and ".join(query_parts)
            logger.debug("Search query: %s", full_query)

            response = await self._exec(self.service.files().list(
                q=full_query,
                pageSize=min(page_size, DRIVE_API_PAGE_SIZE),
                pageToken=page_token,
                fields="nextPageToken, files(id, name, mimeType, size, createdTime, modifiedTime, webViewLink, webContentLink)",
            ))

            files = [
                DriveFileResponse(
                    id=f["id"],
                    name=f["name"],
                    mime_type=f.get("mimeType"),
                    size=int(f["size"]) if f.get("size") else None,
                    created_at=f.get("createdTime"),
                    modified_at=f.get("modifiedTime"),
                    view_link=f.get("webViewLink"),
                    web_content_link=f.get("webContentLink"),
                    download_link=f"https://drive.google.com/uc?export=download&id={f['id']}",
                )
                for f in response.get("files", [])
            ]

            return DriveSearchResponse(
                files=files,
                total=len(files),
                next_page_token=response.get("nextPageToken"),
            )

        except HttpError as error:
            raise DriveAPIError(str(error), operation="search") from error

    async def upload_file(
        self,
        file_content: BinaryIO,
        filename: str,
        folder_id: str | None = None,
        mime_type: str | None = None,
    ) -> DriveFileUploadResponse:
        """Upload a file to Drive.
        
        Files inherit the sharing permissions of their parent folder.
        Ensure the target folder has the appropriate sharing settings.
        """
        try:
            # Detect mime type if not provided
            if not mime_type:
                mime_type, _ = mimetypes.guess_type(filename)
                mime_type = mime_type or "application/octet-stream"

            # Prepare metadata
            file_metadata = {"name": filename}
            if folder_id:
                file_metadata["parents"] = [folder_id]
            elif settings.google_root_folder_id:
                file_metadata["parents"] = [settings.google_root_folder_id]

            # Create media upload
            media = MediaIoBaseUpload(file_content, mimetype=mime_type, resumable=True)

            # Upload
            file = await self._exec(self.service.files().create(
                body=file_metadata,
                media_body=media,
                fields="id, name, size, webViewLink",
            ))

            logger.info("✅ File uploaded: %s (ID: %s)", filename, file['id'])

            self.invalidate_stats_cache()
            self.invalidate_recent_cache()
            return DriveFileUploadResponse(
                id=file["id"],
                name=file["name"],
                size=int(file["size"]) if file.get("size") else None,
                view_link=file.get("webViewLink", f"https://drive.google.com/file/d/{file['id']}/view"),
                download_link=f"https://drive.google.com/uc?export=download&id={file['id']}",
            )

        except HttpError as error:
            if error.resp.status == 403 and "storageQuotaExceeded" in str(error):
                raise QuotaExceededError() from error
            raise FileUploadError(str(error), filename=filename) from error

    async def upload_file_from_path(
        self,
        file_path: str,
        folder_id: str | None = None,
        custom_name: str | None = None,
    ) -> DriveFileUploadResponse:
        """Upload a file from local path."""
        path = Path(file_path)
        if not path.exists():
            raise FileUploadError(f"File not found: {file_path}", filename=path.name)

        try:
            mime_type, _ = mimetypes.guess_type(str(path))
            mime_type = mime_type or "application/octet-stream"

            file_metadata = {"name": custom_name or path.name}
            if folder_id:
                file_metadata["parents"] = [folder_id]
            elif settings.google_root_folder_id:
                file_metadata["parents"] = [settings.google_root_folder_id]

            media = MediaFileUpload(str(path), mimetype=mime_type, resumable=True)

            file = await self._exec(self.service.files().create(
                body=file_metadata,
                media_body=media,
                fields="id, name, size, webViewLink",
            ))

            logger.info("✅ File uploaded from path: %s (ID: %s)", path.name, file['id'])

            self.invalidate_stats_cache()
            self.invalidate_recent_cache()
            return DriveFileUploadResponse(
                id=file["id"],
                name=file["name"],
                size=int(file["size"]) if file.get("size") else None,
                view_link=file.get("webViewLink", f"https://drive.google.com/file/d/{file['id']}/view"),
                download_link=f"https://drive.google.com/uc?export=download&id={file['id']}",
            )

        except HttpError as error:
            if error.resp.status == 403 and "storageQuotaExceeded" in str(error):
                raise QuotaExceededError() from error
            raise FileUploadError(str(error), filename=path.name) from error

    async def delete_file(self, file_id: str) -> bool:
        """Delete a file or folder (root folder is protected)."""
        if file_id == settings.google_root_folder_id:
            raise DriveAPIError(
                "Cannot delete the root folder", operation="delete"
            )
        try:
            await self._exec(self.service.files().delete(fileId=file_id))
            logger.info("🗑️ File deleted: %s", file_id)
            self.invalidate_stats_cache()
            self.invalidate_recent_cache()
            return True
        except HttpError as error:
            if error.resp.status == 404:
                raise EntityNotFoundError("File", file_id) from error
            raise DriveAPIError(str(error), operation="delete") from error

    # =========================================================================
    # Folder Operations
    # =========================================================================

    async def create_folder(
        self,
        name: str,
        parent_folder_id: str | None = None,
        share: bool = True,
    ) -> DriveFolderCreateResponse:
        """Create a folder in Drive."""
        try:
            file_metadata = {
                "name": name,
                "mimeType": FOLDER_MIME_TYPE,
            }
            if parent_folder_id:
                file_metadata["parents"] = [parent_folder_id]
            elif settings.google_root_folder_id:
                file_metadata["parents"] = [settings.google_root_folder_id]

            folder = await self._exec(self.service.files().create(
                body=file_metadata,
                fields="id, name, webViewLink",
            ))

            share_link = None
            if share:
                # Make folder shareable with link
                share_link = await self._create_share_link(folder["id"])

            logger.info("📁 Folder created: %s (ID: %s)", name, folder['id'])

            self.invalidate_stats_cache()
            self.invalidate_recent_cache()
            return DriveFolderCreateResponse(
                id=folder["id"],
                name=folder["name"],
                share_link=share_link,
            )

        except HttpError as error:
            raise DriveAPIError(str(error), operation="create_folder") from error

    async def get_folder(self, folder_id: str) -> DriveFolderResponse:
        """Get folder information."""
        is_root = folder_id == settings.google_root_folder_id
        try:
            folder = await self._exec(self.service.files().get(
                fileId=folder_id,
                fields="id, name, mimeType, createdTime, webViewLink",
                supportsAllDrives=True,
            ))

            # Skip mimeType check for the configured root folder
            if not is_root and folder.get("mimeType") != FOLDER_MIME_TYPE:
                raise EntityNotFoundError("Folder", folder_id)

            # Count files in folder
            files_response = await self._exec(self.service.files().list(
                q=f"'{folder_id}' in parents and trashed=false",
                fields="files(id)",
            ))
            files_count = len(files_response.get("files", []))

            return DriveFolderResponse(
                id=folder["id"],
                name=folder["name"],
                created_at=folder.get("createdTime"),
                share_link=folder.get("webViewLink"),
                files_count=files_count,
            )

        except HttpError as error:
            if error.resp.status == 404:
                raise EntityNotFoundError("Folder", folder_id) from error
            raise DriveAPIError(str(error), operation="get_folder") from error

    async def get_folder_link(self, folder_id: str) -> str:
        """Get shareable link for a folder."""
        is_root = folder_id == settings.google_root_folder_id
        try:
            # First verify it's a folder
            folder = await self._exec(self.service.files().get(
                fileId=folder_id,
                fields="id, mimeType, webViewLink",
                supportsAllDrives=True,
            ))

            if not is_root and folder.get("mimeType") != FOLDER_MIME_TYPE:
                raise EntityNotFoundError("Folder", folder_id)

            # Return existing link or create share permission
            if folder.get("webViewLink"):
                return folder["webViewLink"]

            return await self._create_share_link(folder_id)

        except HttpError as error:
            if error.resp.status == 404:
                raise EntityNotFoundError("Folder", folder_id) from error
            raise DriveAPIError(str(error), operation="get_folder_link") from error

    async def _create_share_link(self, file_id: str) -> str:
        """Create a shareable link for a file or folder."""
        try:
            # Create "anyone with link can view" permission
            await self._exec(self.service.permissions().create(
                fileId=file_id,
                body={"type": "anyone", "role": "reader"},
                fields="id",
            ))

            # Get the updated webViewLink
            file = await self._exec(self.service.files().get(
                fileId=file_id,
                fields="webViewLink",
            ))

            return file.get("webViewLink", f"https://drive.google.com/drive/folders/{file_id}")

        except HttpError as error:
            logger.warning("Could not create share link: %s", error)
            return f"https://drive.google.com/drive/folders/{file_id}"

    # =========================================================================
    # Batch Operations
    # =========================================================================

    async def find_existing(
        self,
        filename: str,
        folder_id: str | None = None,
    ) -> DriveFileResponse | None:
        """Search for a file by exact name in a folder.

        Returns the first match or ``None``.  Uses Google Drive
        ``name = '<filename>'`` (exact match), scoped to *folder_id*
        or the configured root folder.
        """
        try:
            # Escape single quotes in the filename for the Drive query
            safe_name = filename.replace("'", "\\'")
            effective_folder = folder_id or settings.google_root_folder_id

            q_parts = [
                f"name = '{safe_name}'",
                "trashed = false",
            ]
            if effective_folder:
                q_parts.append(f"'{effective_folder}' in parents")

            response = await self._exec(self.service.files().list(
                q=" and ".join(q_parts),
                pageSize=1,
                fields="files(id, name, mimeType, size, createdTime, modifiedTime, webViewLink, webContentLink)",
            ))

            files = response.get("files", [])
            if not files:
                return None

            f = files[0]
            return DriveFileResponse(
                id=f["id"],
                name=f["name"],
                mime_type=f.get("mimeType"),
                size=int(f["size"]) if f.get("size") else None,
                created_at=f.get("createdTime"),
                modified_at=f.get("modifiedTime"),
                view_link=f.get("webViewLink"),
                web_content_link=f.get("webContentLink"),
                download_link=f"https://drive.google.com/uc?export=download&id={f['id']}",
            )
        except HttpError:
            return None

    async def upload_batch(
        self,
        files: list[tuple[BinaryIO, str]],  # (content, filename)
        folder_id: str | None = None,
        skip_existing: bool = False,
    ) -> BatchUploadResponse:
        """Upload multiple files.

        When *skip_existing* is ``True``, each file is first searched by
        exact name inside *folder_id* (or root).  If a match is found the
        existing file metadata is returned (``existed=True``) and the
        upload is skipped.  This avoids duplicates without extra
        orchestration from the caller.
        """
        results: list[BatchUploadItem] = []
        successful = 0
        failed = 0

        for content, filename in files:
            try:
                # Check for existing file when dedup is enabled
                if skip_existing:
                    existing = await self.find_existing(filename, folder_id)
                    if existing:
                        logger.debug(
                            "⏭️ File already exists, skipping: %s (ID: %s)",
                            filename, existing.id,
                        )
                        results.append(BatchUploadItem(
                            filename=filename,
                            success=True,
                            file_id=existing.id,
                            view_link=existing.view_link,
                            existed=True,
                        ))
                        successful += 1
                        continue

                result = await self.upload_file(content, filename, folder_id)
                results.append(BatchUploadItem(
                    filename=filename,
                    success=True,
                    file_id=result.id,
                    view_link=result.view_link,
                ))
                successful += 1
            except Exception as e:
                results.append(BatchUploadItem(
                    filename=filename,
                    success=False,
                    error=str(e),
                ))
                failed += 1
                logger.warning("Failed to upload %s: %s", filename, e)

        return BatchUploadResponse(
            total=len(files),
            successful=successful,
            failed=failed,
            items=results,
        )

    # =========================================================================
    # Quota
    # =========================================================================

    async def get_quota(self) -> DriveQuotaResponse:
        """Get Drive storage quota."""
        try:
            about = await self._exec(self.service.about().get(
                fields="storageQuota",
            ))

            quota = about.get("storageQuota", {})
            total = int(quota.get("limit", 0))
            used = int(quota.get("usage", 0))
            available = total - used if total > 0 else 0
            usage_percent = (used / total * 100) if total > 0 else 0

            return DriveQuotaResponse(
                total_bytes=total,
                used_bytes=used,
                available_bytes=available,
                usage_percent=round(usage_percent, 2),
                auth_mode=self._auth_mode,
            )

        except HttpError as error:
            raise DriveAPIError(str(error), operation="get_quota") from error

    # =========================================================================
    # Rename
    # =========================================================================

    async def rename(self, file_id: str, new_name: str) -> None:
        """Rename a file or folder (root folder is protected)."""
        if file_id == settings.google_root_folder_id:
            raise DriveAPIError(
                "Cannot rename the root folder", operation="rename"
            )
        try:
            await self._exec(
                self.service.files().update(
                    fileId=file_id,
                    body={"name": new_name},
                    fields="id",
                )
            )
            logger.info("✏️ Renamed %s → %s", file_id, new_name)
        except HttpError as error:
            if error.resp.status == 404:
                raise EntityNotFoundError("File/Folder", file_id) from error
            raise DriveAPIError(str(error), operation="rename") from error

    # =========================================================================
    # Stats (with cache)
    # =========================================================================

    _stats_cache: dict = {"data": None, "ts": 0.0}
    _STATS_TTL = 60  # seconds
    _stats_lock: asyncio.Lock | None = None

    @classmethod
    def _get_stats_lock(cls) -> asyncio.Lock:
        if cls._stats_lock is None:
            cls._stats_lock = asyncio.Lock()
        return cls._stats_lock

    async def get_stats(self) -> DriveStatsResponse:
        """Get Drive usage statistics scoped to the root folder (cached 60s)."""
        now = __import__("time").monotonic()
        cache = DriveService._stats_cache

        if cache["data"] is not None and (now - cache["ts"]) <= DriveService._STATS_TTL:
            return cache["data"]

        lock = DriveService._get_stats_lock()
        async with lock:
            # Double-check after acquiring lock
            now = __import__("time").monotonic()
            if cache["data"] is not None and (now - cache["ts"]) <= DriveService._STATS_TTL:
                return cache["data"]

            result = await self._fetch_stats()
            cache["data"] = result
            cache["ts"] = __import__("time").monotonic()
            return result

    async def _fetch_stats(self) -> DriveStatsResponse:
        """Actually query Google Drive for stats (parallel queries)."""
        try:
            root = settings.google_root_folder_id

            # Build queries
            file_query = "trashed=false and mimeType!='application/vnd.google-apps.folder'"
            folder_query = "trashed=false and mimeType='application/vnd.google-apps.folder'"
            if root:
                file_query = f"'{root}' in parents and " + file_query
                folder_query = f"'{root}' in parents and " + folder_query

            # NOTE: with the _exec lock these run sequentially;
            # gather still collects both results conveniently.
            files_resp, folders_resp = await asyncio.gather(
                self._exec(self.service.files().list(
                    q=file_query, fields="files(id)", pageSize=1000,
                )),
                self._exec(self.service.files().list(
                    q=folder_query, fields="files(id)", pageSize=1000,
                )),
            )

            return DriveStatsResponse(
                total_files=len(files_resp.get("files", [])),
                total_folders=len(folders_resp.get("files", [])),
            )
        except HttpError as error:
            raise DriveAPIError(str(error), operation="get_stats") from error

    def invalidate_stats_cache(self) -> None:
        """Clear stats cache (call after uploads, deletes, folder creation)."""
        DriveService._stats_cache = {"data": None, "ts": 0.0}

    # =========================================================================
    # Recent files cache
    # =========================================================================

    _recent_cache: dict = {"data": None, "ts": 0.0}
    _RECENT_TTL = 30  # seconds
    _recent_lock: asyncio.Lock | None = None

    @classmethod
    def _get_recent_lock(cls) -> asyncio.Lock:
        if cls._recent_lock is None:
            cls._recent_lock = asyncio.Lock()
        return cls._recent_lock

    async def get_recent_files(self, limit: int = 10) -> DriveSearchResponse:
        """Get recent files (cached 30s). Used by dashboard overview."""
        import time as _time
        now = _time.monotonic()
        cache = DriveService._recent_cache

        if cache["data"] is not None and (now - cache["ts"]) <= DriveService._RECENT_TTL:
            return cache["data"]

        lock = DriveService._get_recent_lock()
        async with lock:
            now = _time.monotonic()
            if cache["data"] is not None and (now - cache["ts"]) <= DriveService._RECENT_TTL:
                return cache["data"]

            result = await self.search_files(page_size=limit)
            cache["data"] = result
            cache["ts"] = _time.monotonic()
            return result

    def invalidate_recent_cache(self) -> None:
        """Clear recent files cache."""
        DriveService._recent_cache = {"data": None, "ts": 0.0}
