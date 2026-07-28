"""Shared storage abstractions for Cognityx platform components."""

from cognityx_storage.backend import StorageBackend
from cognityx_storage.blob import BlobRef, BlobStore, ContentDigest, PreparedBlob
from cognityx_storage.blob_gc import BlobGarbageCollector, BlobGcCandidate, BlobGcPlan, BlobGcResult
from cognityx_storage.cas import (
    SUPPORTED_DEDUP_SCOPES,
    build_cas_key,
    derive_blob_id,
    hash_file,
    hash_stream,
    resolve_dedup_domain,
)
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
from cognityx_storage.runtime import (
    ResolvedRoleStore,
    StorageRoleResolution,
    StorageRoleResolver,
    StorageRuntime,
)

__all__ = [
    "DEFAULT_STORAGE_ROOT",
    "BlobRef",
    "BlobGarbageCollector",
    "BlobGcCandidate",
    "BlobGcPlan",
    "BlobGcResult",
    "BlobStore",
    "ContentDigest",
    "InvalidStorageKeyError",
    "LocalStorageBackend",
    "ObjectAlreadyExistsError",
    "ObjectConsistencyError",
    "ObjectNotFoundError",
    "PreparedBlob",
    "ResolvedRoleStore",
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
    "StorageRoleResolution",
    "StorageRoleResolver",
    "StorageRoleUnavailableError",
    "StorageRuntime",
    "StorageValidationIssue",
    "StorageValidationReport",
    "StoredObject",
    "SUPPORTED_DEDUP_SCOPES",
    "UnsupportedOperationError",
    "default_backend_factory",
    "build_cas_key",
    "derive_blob_id",
    "hash_file",
    "hash_stream",
    "resolve_dedup_domain",
]
