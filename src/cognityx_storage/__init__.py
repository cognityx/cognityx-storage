"""Shared storage abstractions for Cognityx platform components."""

from cognityx_storage.backend import StorageBackend
from cognityx_storage.client import StorageClient
from cognityx_storage.exceptions import (
    InvalidStorageKeyError,
    ObjectAlreadyExistsError,
    ObjectConsistencyError,
    ObjectNotFoundError,
    StorageError,
    UnsupportedOperationError,
)
from cognityx_storage.local import (
    DEFAULT_STORAGE_ROOT,
    LocalStorageBackend,
)
from cognityx_storage.models import StoredObject

__all__ = [
    "DEFAULT_STORAGE_ROOT",
    "InvalidStorageKeyError",
    "LocalStorageBackend",
    "ObjectAlreadyExistsError",
    "ObjectConsistencyError",
    "ObjectNotFoundError",
    "StorageBackend",
    "StorageClient",
    "StorageError",
    "StoredObject",
    "UnsupportedOperationError",
]
