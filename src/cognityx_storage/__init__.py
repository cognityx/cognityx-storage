"""Shared storage abstractions for Cognityx platform components."""

from cognityx_storage.backend import StorageBackend
from cognityx_storage.capabilities import StorageCapabilities
from cognityx_storage.client import StorageClient
from cognityx_storage.config import (
    StorageConfig,
    StorageProfile,
    StorageRole,
    StorageValidationIssue,
    StorageValidationReport,
)
from cognityx_storage.exceptions import (
    InvalidStorageKeyError,
    ObjectAlreadyExistsError,
    ObjectConsistencyError,
    ObjectNotFoundError,
    StorageConfigurationError,
    StorageError,
    StorageProviderUnavailableError,
    StorageRoleNotFoundError,
    StorageRoleUnavailableError,
    UnsupportedOperationError,
)
from cognityx_storage.factory import StorageBackendFactory, default_backend_factory
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
    "StorageBackendFactory",
    "StorageCapabilities",
    "StorageClient",
    "StorageConfig",
    "StorageConfigurationError",
    "StorageError",
    "StorageProfile",
    "StorageProviderUnavailableError",
    "StorageRole",
    "StorageRoleNotFoundError",
    "StorageRoleUnavailableError",
    "StorageValidationIssue",
    "StorageValidationReport",
    "StoredObject",
    "UnsupportedOperationError",
    "default_backend_factory",
]
