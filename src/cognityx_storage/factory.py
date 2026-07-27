"""Minimal registry that maps configured profile types to backend builders."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from cognityx_storage.backend import StorageBackend
from cognityx_storage.capabilities import (
    FILESYSTEM_CAPABILITIES,
    StorageCapabilities,
)
from cognityx_storage.exceptions import StorageProviderUnavailableError
from cognityx_storage.local import LocalStorageBackend

if TYPE_CHECKING:
    from cognityx_storage.config import StorageProfile


BackendBuilder = Callable[["StorageProfile"], StorageBackend]


class StorageBackendFactory:
    """Build backends through an explicit, dependency-light provider registry."""

    def __init__(self) -> None:
        self._builders: dict[str, BackendBuilder] = {}
        self._capabilities: dict[str, StorageCapabilities] = {}

    def register(
        self,
        profile_type: str,
        builder: BackendBuilder,
        *,
        capabilities: StorageCapabilities | None = None,
    ) -> None:
        """Register one provider builder."""
        if not profile_type.strip():
            raise ValueError("Storage profile type cannot be empty.")
        self._builders[profile_type] = builder
        self._capabilities[profile_type] = capabilities or StorageCapabilities()

    def is_available(self, profile_type: str) -> bool:
        """Return whether this process has an implementation for a profile type."""
        return profile_type in self._builders

    def capabilities(self, profile_type: str) -> StorageCapabilities:
        """Return capabilities actually supplied by the registered provider."""
        return self._capabilities.get(profile_type, StorageCapabilities())

    def build(self, profile: "StorageProfile") -> StorageBackend:
        """Build one configured backend or report that its provider is unavailable."""
        builder = self._builders.get(profile.type)
        if builder is None:
            raise StorageProviderUnavailableError(
                f"Storage provider implementation is unavailable for profile "
                f"'{profile.name}' (type '{profile.type}')."
            )
        return builder(profile)


def default_backend_factory() -> StorageBackendFactory:
    """Return the built-in registry containing the filesystem provider."""
    factory = StorageBackendFactory()
    factory.register(
        "filesystem",
        lambda profile: LocalStorageBackend(profile.options["root"]),
        capabilities=FILESYSTEM_CAPABILITIES,
    )
    return factory
