"""Filesystem implementation of the Cognityx storage backend."""

from __future__ import annotations

import mimetypes
import os
from pathlib import Path, PurePosixPath
import shutil
import tempfile
from typing import BinaryIO

from cognityx_storage.exceptions import (
    InvalidStorageKeyError,
    ObjectAlreadyExistsError,
    ObjectNotFoundError,
    UnsupportedOperationError,
)
from cognityx_storage.models import StoredObject

DEFAULT_STORAGE_ROOT = Path("/mnt/d/AI/cognitive/cognityx-storage")
_DIRECTORY_MEDIA_TYPE = "application/x-directory"
_KNOWN_MEDIA_TYPES = {
    ".jsonl": "application/x-ndjson",
    ".parquet": "application/vnd.apache.parquet",
}


def validate_storage_key(key: str, *, allow_empty: bool = False) -> str:
    """Return a normalized portable key or reject unsafe path syntax."""
    if not isinstance(key, str):
        raise InvalidStorageKeyError("Storage keys must be strings.")
    if "\\" in key:
        raise InvalidStorageKeyError("Storage keys must use '/' separators.")
    if any(ord(character) < 32 for character in key):
        raise InvalidStorageKeyError("Storage keys cannot contain control characters.")

    if not key:
        if allow_empty:
            return ""
        raise InvalidStorageKeyError("Storage keys cannot be empty.")
    if key.startswith("/"):
        raise InvalidStorageKeyError("Storage keys must be relative.")

    parts = key.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise InvalidStorageKeyError(
            "Storage keys must be relative and cannot contain '.' or '..'."
        )
    path = PurePosixPath(*parts)
    return path.as_posix()


def _directory_size(path: Path) -> int:
    return sum(
        child.stat().st_size
        for child in path.rglob("*")
        if child.is_file()
    )


def _media_type_for(path: Path) -> str:
    return (
        _KNOWN_MEDIA_TYPES.get(path.suffix.lower())
        or mimetypes.guess_type(path.name)[0]
        or "application/octet-stream"
    )


class LocalStorageBackend:
    """Store logical objects beneath a configurable filesystem root."""

    def __init__(self, root: str | Path = DEFAULT_STORAGE_ROOT) -> None:
        self.root = Path(root).expanduser().resolve()

    def _path(self, key: str, *, allow_empty: bool = False) -> Path:
        normalized = validate_storage_key(key, allow_empty=allow_empty)
        candidate = (
            self.root
            if not normalized
            else self.root.joinpath(*normalized.split("/")).resolve(strict=False)
        )
        if not candidate.is_relative_to(self.root):
            raise InvalidStorageKeyError(
                "Storage key resolves outside the configured storage root."
            )
        return candidate

    def _key_for_path(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    def _ensure_absent(self, path: Path, key: str) -> None:
        if path.exists():
            raise ObjectAlreadyExistsError(f"Storage object already exists: {key}")

    def put_stream(
        self,
        key: str,
        source: BinaryIO,
        *,
        media_type: str = "application/octet-stream",
    ) -> StoredObject:
        normalized = validate_storage_key(key)
        destination = self._path(normalized)
        self._ensure_absent(destination, normalized)
        destination.parent.mkdir(parents=True, exist_ok=True)

        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=destination.parent,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as output:
                shutil.copyfileobj(source, output)
                output.flush()
                os.fsync(output.fileno())
            # ``Path.replace`` can overwrite another writer that published
            # between the preflight check and this point.  Linking creates the
            # destination atomically and fails when an immutable object won
            # the race, preserving no-overwrite publication semantics.
            try:
                os.link(temporary, destination)
            except FileExistsError as exc:
                raise ObjectAlreadyExistsError(
                    f"Storage object already exists: {normalized}"
                ) from exc
            temporary.unlink()
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        return self._describe(destination, normalized, media_type=media_type)

    def put_file(
        self,
        key: str,
        source: str | Path,
        *,
        media_type: str | None = None,
    ) -> StoredObject:
        source_path = Path(source)
        if not source_path.is_file():
            raise ObjectNotFoundError(f"Source file does not exist: {source_path}")
        detected = media_type or _media_type_for(source_path)
        with source_path.open("rb") as input_file:
            return self.put_stream(
                key,
                input_file,
                media_type=detected,
            )

    def put_directory(self, key: str, source: str | Path) -> StoredObject:
        normalized = validate_storage_key(key)
        source_path = Path(source)
        if not source_path.is_dir():
            raise ObjectNotFoundError(f"Source directory does not exist: {source_path}")

        destination = self._path(normalized)
        self._ensure_absent(destination, normalized)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(
            tempfile.mkdtemp(
                prefix=f".{destination.name}.",
                suffix=".tmp",
                dir=destination.parent,
            )
        )
        try:
            shutil.rmtree(temporary)
            shutil.copytree(source_path, temporary)
            self._ensure_absent(destination, normalized)
            temporary.replace(destination)
        except BaseException:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
        return self._describe(destination, normalized)

    def open_reader(self, key: str) -> BinaryIO:
        path = self._existing_path(key)
        if path.is_dir():
            raise UnsupportedOperationError(
                f"Cannot open a directory as a binary stream: {key}"
            )
        return path.open("rb")

    def materialize(self, key: str) -> Path:
        return self._existing_path(key)

    def resolve_local_path(self, key: str) -> Path | None:
        """Return the existing local object path without any materialization."""
        return self._existing_path(key)

    def native_path(self, key: str) -> Path:
        """Return the safe filesystem target for a possibly uncreated object."""
        return self._path(key)

    def stat(self, key: str) -> StoredObject:
        normalized = validate_storage_key(key)
        return self._describe(self._existing_path(normalized), normalized)

    def exists(self, key: str) -> bool:
        try:
            path = self._path(key)
        except InvalidStorageKeyError:
            raise
        return path.exists()

    def list(self, prefix: str = "") -> tuple[StoredObject, ...]:
        normalized = validate_storage_key(prefix, allow_empty=True)
        directory = self._path(normalized, allow_empty=True)
        if not directory.exists():
            raise ObjectNotFoundError(f"Storage prefix does not exist: {prefix}")
        if not directory.is_dir():
            return (self._describe(directory, normalized),)
        return tuple(
            self._describe(child, self._key_for_path(child))
            for child in sorted(directory.iterdir(), key=lambda value: value.name)
            if not child.name.startswith(".")
        )

    def delete(self, key: str, *, recursive: bool = False) -> None:
        """Remove a logical file or an explicitly approved directory tree."""
        path = self._existing_path(key)
        if path.is_dir():
            if not recursive:
                raise UnsupportedOperationError(
                    f"Deleting a directory requires recursive=True: {key}"
                )
            shutil.rmtree(path)
            return
        path.unlink()

    def _existing_path(self, key: str) -> Path:
        normalized = validate_storage_key(key)
        path = self._path(normalized)
        if not path.exists():
            raise ObjectNotFoundError(f"Storage object does not exist: {normalized}")
        return path

    def _describe(
        self,
        path: Path,
        key: str,
        *,
        media_type: str | None = None,
    ) -> StoredObject:
        is_directory = path.is_dir()
        resolved_media_type = (
            _DIRECTORY_MEDIA_TYPE
            if is_directory
            else media_type or _media_type_for(path)
        )
        return StoredObject(
            key=key,
            uri=path.resolve().as_uri(),
            size_bytes=_directory_size(path) if is_directory else path.stat().st_size,
            media_type=resolved_media_type,
            is_directory=is_directory,
        )
