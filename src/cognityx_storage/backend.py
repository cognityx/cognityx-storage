"""Provider contract implemented by local and future remote storage backends."""

from pathlib import Path
from typing import BinaryIO, Protocol, runtime_checkable

from cognityx_storage.models import StoredObject


@runtime_checkable
class StorageBackend(Protocol):
    """Persist and retrieve content by provider-neutral logical keys."""

    def put_stream(
        self,
        key: str,
        source: BinaryIO,
        *,
        media_type: str = "application/octet-stream",
    ) -> StoredObject:
        """Publish a binary stream without replacing an existing object."""
        ...

    def put_file(
        self,
        key: str,
        source: str | Path,
        *,
        media_type: str | None = None,
    ) -> StoredObject:
        """Publish one local file."""
        ...

    def put_directory(self, key: str, source: str | Path) -> StoredObject:
        """Publish a local directory tree."""
        ...

    def open_reader(self, key: str) -> BinaryIO:
        """Open a stored file for binary reading."""
        ...

    def materialize(self, key: str) -> Path:
        """Return a local path containing the requested content."""
        ...

    def stat(self, key: str) -> StoredObject:
        """Describe a stored file or directory."""
        ...

    def exists(self, key: str) -> bool:
        """Return whether a logical key exists."""
        ...

    def list(self, prefix: str = "") -> tuple[StoredObject, ...]:
        """List the immediate children under a logical prefix."""
        ...

    def delete(self, key: str, *, recursive: bool = False) -> None:
        """Delete one logical object, optionally allowing a directory tree."""
        ...
