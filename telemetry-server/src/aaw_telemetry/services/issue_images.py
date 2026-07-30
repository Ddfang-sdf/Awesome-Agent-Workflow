from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import shutil
import threading
import uuid
import warnings
from collections import defaultdict, deque
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path

from PIL import Image, UnidentifiedImageError
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from ..config import Settings
from ..errors import ApiError
from ..models import IssueImage

logger = logging.getLogger("aaw_telemetry.issue_images")

_FORMATS = {
    "PNG": ("image/png", "png"),
    "JPEG": ("image/jpeg", "jpg"),
    "WEBP": ("image/webp", "webp"),
}


def _now() -> datetime:
    return datetime.now(UTC)


class IssueImageRateLimiter:
    def __init__(self, limit: int):
        self.limit = limit
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, client_ip: str, now: float) -> None:
        with self._lock:
            events = self._events[client_ip]
            while events and events[0] <= now - 60:
                events.popleft()
            if len(events) >= self.limit:
                raise ApiError(
                    429,
                    "IMAGE_UPLOAD_RATE_LIMITED",
                    "too many image uploads; retry in one minute",
                    retryable=True,
                )
            events.append(now)


class IssueImageService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.root = (settings.object_storage_dir / "issue-images").resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def create_temporary(self, session: Session, payload: bytes) -> IssueImage:
        if not payload:
            raise ApiError(400, "INVALID_IMAGE", "image file is empty")
        if len(payload) > self.settings.issue_image_max_bytes:
            raise ApiError(413, "IMAGE_TOO_LARGE", "image exceeds the 5 MiB limit")

        temporary_bytes = session.scalar(
            select(func.coalesce(func.sum(IssueImage.size_bytes), 0)).where(
                IssueImage.status == "temporary"
            )
        )
        if int(temporary_bytes or 0) + len(payload) > self.settings.issue_image_temp_quota_bytes:
            raise ApiError(
                503,
                "IMAGE_TEMP_QUOTA_EXCEEDED",
                "temporary image storage is full; retry after cleanup",
                retryable=True,
            )
        if shutil.disk_usage(self.root).free < self.settings.issue_image_min_free_bytes:
            raise ApiError(
                507,
                "IMAGE_STORAGE_LOW",
                "image storage has less than the configured free-space reserve",
            )

        normalized, preview, media_type, extension, width, height = self._normalize(payload)
        image_id = uuid.uuid4()
        directory = self.root / image_id.hex[:2] / image_id.hex
        full_path = directory / f"original.{extension}"
        preview_path = directory / "preview.webp"
        directory.mkdir(parents=True, exist_ok=False)
        try:
            self._atomic_write(full_path, normalized)
            self._atomic_write(preview_path, preview)
            image = IssueImage(
                id=image_id,
                status="temporary",
                media_type=media_type,
                size_bytes=len(normalized),
                preview_size_bytes=len(preview),
                width=width,
                height=height,
                sha256=hashlib.sha256(normalized).hexdigest(),
                full_object_key=self._object_key(full_path),
                preview_object_key=self._object_key(preview_path),
                created_at=_now(),
            )
            session.add(image)
            session.commit()
            return image
        except Exception:
            shutil.rmtree(directory, ignore_errors=True)
            session.rollback()
            raise

    def path_for(self, image: IssueImage, variant: str) -> tuple[Path, str]:
        if variant == "preview":
            key, media_type = image.preview_object_key, "image/webp"
        else:
            key, media_type = image.full_object_key, image.media_type
        path = (self.settings.object_storage_dir.resolve() / key).resolve()
        storage_root = self.settings.object_storage_dir.resolve()
        if storage_root not in path.parents:
            raise ApiError(500, "INTERNAL_ERROR", "invalid issue image object path")
        if not path.is_file():
            logger.error(
                "问题图片文件丢失",
                extra={"event": "issue_image.missing", "image_id": str(image.id)},
            )
            raise ApiError(404, "IMAGE_FILE_NOT_FOUND", "image file is unavailable")
        return path, media_type

    def delete_files(self, image: IssueImage) -> None:
        for key in (image.full_object_key, image.preview_object_key):
            path = (self.settings.object_storage_dir.resolve() / key).resolve()
            if self.settings.object_storage_dir.resolve() in path.parents:
                path.unlink(missing_ok=True)
        directory = (
            self.settings.object_storage_dir.resolve() / image.full_object_key
        ).resolve().parent
        try:
            directory.rmdir()
            directory.parent.rmdir()
        except OSError:
            pass

    def _normalize(self, payload: bytes) -> tuple[bytes, bytes, str, str, int, int]:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                source = Image.open(io.BytesIO(payload))
                image_format = source.format
                if image_format not in _FORMATS:
                    raise ApiError(
                        400,
                        "UNSUPPORTED_IMAGE_TYPE",
                        "only PNG, JPEG and WebP images are supported",
                    )
                width, height = source.size
                if (
                    width > self.settings.issue_image_max_dimension
                    or height > self.settings.issue_image_max_dimension
                    or width * height > self.settings.issue_image_max_pixels
                ):
                    raise ApiError(
                        400,
                        "IMAGE_DIMENSIONS_EXCEEDED",
                        "image dimensions exceed the configured safety limit",
                    )
                if getattr(source, "n_frames", 1) != 1:
                    raise ApiError(
                        400, "ANIMATED_IMAGE_NOT_SUPPORTED", "animated images are not supported"
                    )
                source.load()
                normalized_source = self._safe_mode(source, image_format)
        except ApiError:
            raise
        except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError) as exc:
            raise ApiError(400, "INVALID_IMAGE", "file is not a valid supported image") from exc

        media_type, extension = _FORMATS[image_format]
        full_stream = io.BytesIO()
        self._save(normalized_source, full_stream, image_format)

        preview_source = normalized_source.copy()
        preview_source.thumbnail(
            (
                self.settings.issue_image_preview_dimension,
                self.settings.issue_image_preview_dimension,
            ),
            Image.Resampling.LANCZOS,
        )
        preview_stream = io.BytesIO()
        preview_source.save(preview_stream, format="WEBP", quality=88, method=4)
        return (
            full_stream.getvalue(),
            preview_stream.getvalue(),
            media_type,
            extension,
            width,
            height,
        )

    @staticmethod
    def _safe_mode(source: Image.Image, image_format: str) -> Image.Image:
        if image_format == "JPEG":
            return source.convert("RGB")
        if source.mode not in {"RGB", "RGBA"}:
            return source.convert("RGBA" if "transparency" in source.info else "RGB")
        return source.copy()

    @staticmethod
    def _save(image: Image.Image, stream: io.BytesIO, image_format: str) -> None:
        if image_format == "PNG":
            image.save(stream, format="PNG", optimize=True)
        elif image_format == "JPEG":
            image.save(stream, format="JPEG", quality=95, optimize=True)
        else:
            image.save(stream, format="WEBP", quality=95, method=4)

    def _object_key(self, path: Path) -> str:
        return path.relative_to(self.settings.object_storage_dir.resolve()).as_posix()

    @staticmethod
    def _atomic_write(path: Path, content: bytes) -> None:
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.uploading")
        temporary.write_bytes(content)
        temporary.replace(path)


class IssueImageJanitor:
    def __init__(self, factory: sessionmaker[Session], settings: Settings):
        self.factory = factory
        self.settings = settings
        self.service = IssueImageService(settings)
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        while not self._stop.is_set():
            await asyncio.to_thread(self.cleanup_once)
            with suppress(TimeoutError):
                await asyncio.wait_for(
                    self._stop.wait(),
                    timeout=self.settings.issue_image_cleanup_interval_seconds,
                )

    def cleanup_once(self) -> int:
        now = _now()
        temporary_cutoff = now - timedelta(seconds=self.settings.issue_image_temp_seconds)
        with self.factory() as session:
            images = session.scalars(
                select(IssueImage).where(
                    or_(
                        (
                            (IssueImage.status == "temporary")
                            & (IssueImage.created_at <= temporary_cutoff)
                        ),
                        (
                            (IssueImage.status == "pending_delete")
                            & (IssueImage.delete_after <= now)
                        ),
                    )
                )
            ).all()
            for image in images:
                self.service.delete_files(image)
                session.delete(image)
            session.commit()
        if images:
            logger.info(
                "已清理过期问题图片",
                extra={"event": "issue_image.cleanup", "count": len(images)},
            )
        return len(images)
