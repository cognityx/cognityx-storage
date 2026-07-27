"""Application-facing storage client with shared and user scopes."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any, BinaryIO

from cognityx_storage.backend import StorageBackend
from cognityx_storage.exceptions import (
    InvalidStorageKeyError,
    ObjectAlreadyExistsError,
    ObjectConsistencyError,
    UnsupportedOperationError,
)
from cognityx_storage.local import LocalStorageBackend, validate_storage_key
from cognityx_storage.models import StoredObject


class StorageClient:
    """Expose logical storage operations without revealing backend layout."""

    def __init__(
        self,
        backend: StorageBackend | None = None,
        *,
        scope: str = "",
    ) -> None:
        self._backend = backend or LocalStorageBackend()
        self._scope = validate_storage_key(scope, allow_empty=True)

    def for_shared_data(self) -> "StorageClient":
        """Return a client restricted to the shared namespace."""
        return StorageClient(self._backend, scope="shared")

    def for_user(self, user_id: str) -> "StorageClient":
        """Return a client restricted to one user's namespace."""
        segment = validate_storage_key(user_id)
        if "/" in segment:
            raise InvalidStorageKeyError("User identifiers must be one path segment.")
        return StorageClient(self._backend, scope=f"users/{segment}")

    def put_bytes(
        self,
        key: str,
        content: bytes,
        *,
        media_type: str = "application/octet-stream",
    ) -> StoredObject:
        """Publish an in-memory byte string."""
        return self.put_stream(key, io.BytesIO(content), media_type=media_type)

    def put_json(self, key: str, value: Any) -> StoredObject:
        """Serialize and publish a JSON value using UTF-8."""
        content = (
            json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        ).encode("utf-8")
        return self.put_bytes(key, content, media_type="application/json")

    def put_json_idempotent(self, key: str, value: Any) -> StoredObject:
        """Publish immutable JSON, accepting only an identical existing value."""
        content = (
            json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        ).encode("utf-8")
        try:
            return self.put_bytes(key, content, media_type="application/json")
        except ObjectAlreadyExistsError:
            with self.open(key) as existing:
                if existing.read() != content:
                    raise ObjectConsistencyError(
                        f"Immutable JSON object conflicts with requested value: {key}"
                    ) from None
            return self.stat(key)

    def put_stream(
        self,
        key: str,
        source: BinaryIO,
        *,
        media_type: str = "application/octet-stream",
    ) -> StoredObject:
        """Publish a binary stream."""
        return self._backend.put_stream(
            self._scoped_key(key), source, media_type=media_type
        )

    def put_file(
        self,
        key: str,
        source: str | Path,
        *,
        media_type: str | None = None,
    ) -> StoredObject:
        """Publish one local file."""
        return self._backend.put_file(
            self._scoped_key(key), source, media_type=media_type
        )

    def put_directory(self, key: str, source: str | Path) -> StoredObject:
        """Publish a local directory tree."""
        return self._backend.put_directory(self._scoped_key(key), source)

    def open(self, key: str) -> BinaryIO:
        """Open one stored file for binary reading."""
        return self._backend.open_reader(self._scoped_key(key))

    def materialize(self, key: str) -> Path:
        """Return a local path for a stored file or directory."""
        return self._backend.materialize(self._scoped_key(key))

    def resolve_local_path(self, key: str) -> Path | None:
        """Return an existing native path without downloading or materializing."""
        resolver = getattr(self._backend, "resolve_local_path", None)
        if resolver is None:
            return None
        return resolver(self._scoped_key(key))

    def native_path(self, key: str) -> Path:
        """Return a native target path, including for content not created yet."""
        resolver = getattr(self._backend, "native_path", None)
        if resolver is None:
            raise UnsupportedOperationError(
                f"Backend {self.backend_name} does not provide native paths."
            )
        return resolver(self._scoped_key(key))

    def uri(self, key: str) -> str:
        """Return the provider-neutral Cognityx URI for a scoped logical key."""
        return f"storage://{self._scoped_key(key)}"

    @property
    def backend_name(self) -> str:
        """Backend identity for diagnostics without exposing backend internals."""
        return type(self._backend).__name__

    def stat(self, key: str) -> StoredObject:
        """Describe a stored file or directory."""
        return self._backend.stat(self._scoped_key(key))

    def exists(self, key: str) -> bool:
        """Return whether a logical key exists in this client's scope."""
        return self._backend.exists(self._scoped_key(key))

    def list(self, prefix: str = "") -> tuple[StoredObject, ...]:
        """List immediate children within this client's scope."""
        return self._backend.list(self._scoped_key(prefix, allow_empty=True))

    def delete(self, key: str, *, recursive: bool = False) -> None:
        """Delete one scoped object without exposing backend paths."""
        self._backend.delete(self._scoped_key(key), recursive=recursive)

    def _scoped_key(self, key: str, *, allow_empty: bool = False) -> str:
        normalized = validate_storage_key(key, allow_empty=allow_empty)
        if self._scope and normalized:
            return f"{self._scope}/{normalized}"
        if self._scope:
            return self._scope
        return normalized
