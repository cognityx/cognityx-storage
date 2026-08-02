"""Role-to-profile runtime routing above the existing :class:`StorageClient`."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, BinaryIO

from cognityx_storage.backend import StorageBackend
from cognityx_storage.blob import BlobRef, BlobStore
from cognityx_storage.capabilities import StorageCapabilities
from cognityx_storage.client import StorageClient
from cognityx_storage.config import StorageConfig, StorageProfile, StorageRole
from cognityx_storage.exceptions import (
    ObjectNotFoundError,
    StorageConfigurationError,
    StorageProviderUnavailableError,
    StorageRoleNotFoundError,
    StorageRoleUnavailableError,
)
from cognityx_storage.factory import (
    StorageBackendFactory,
    default_backend_factory,
)
from cognityx_storage.local import validate_storage_key
from cognityx_storage.models import StoredObject


@dataclass(frozen=True, slots=True)
class StorageRoleResolution:
    """One deterministic role-to-profile selection."""

    role: StorageRole
    requested_profile: str
    resolved_profile: StorageProfile
    profiles_tried: tuple[str, ...]
    reason: str | None
    warnings: tuple[str, ...]
    capabilities: StorageCapabilities

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role.name,
            "requested_profile": self.requested_profile,
            "resolved_profile": self.resolved_profile.name,
            "fallback_profiles": list(self.role.fallback_profiles),
            "profiles_tried": list(self.profiles_tried),
            "namespace": self.role.namespace,
            "dedup_scope": self.role.dedup_scope,
            "reason": self.reason,
            "warnings": list(self.warnings),
            "capabilities": self.capabilities.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class StorageLocation:
    """Canonical location and reachability details for one storage URI."""

    uri: str
    backend_name: str
    profile_name: str
    role_name: str | None
    local_path: str | None
    exists: bool
    size_bytes: int | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "uri": self.uri,
            "backend": self.backend_name,
            "role": self.role_name,
            "profile_name": self.profile_name,
            "role_name": self.role_name,
            "local_path": self.local_path,
            "exists": self.exists,
            "size_bytes": self.size_bytes,
        }


class StorageRoleResolver:
    """Select the first configured profile with an installed provider."""

    def __init__(
        self,
        config: StorageConfig,
        factory: StorageBackendFactory,
    ) -> None:
        self._config = config
        self._factory = factory

    def resolve(self, role_name: str) -> StorageRoleResolution:
        role = self._config.roles.get(role_name)
        if role is None:
            raise StorageRoleNotFoundError(
                f"Storage role is not configured: {role_name}"
            )
        if role.profile is None:
            raise StorageRoleUnavailableError(
                f"Storage role '{role.name}' has no primary profile."
            )
        candidates = (role.profile, *role.fallback_profiles)
        selected = next(
            (
                self._config.profiles[name]
                for name in candidates
                if name in self._config.profiles
                and self._factory.is_available(self._config.profiles[name].type)
            ),
            None,
        )
        if selected is None:
            details = ", ".join(
                f"{name} ({self._unavailable_reason(name)})" for name in candidates
            )
            raise StorageRoleUnavailableError(
                f"Storage role '{role.name}' has no usable profile. "
                f"Profiles tried: {details}."
            )

        capabilities = self._factory.capabilities(selected.type)
        missing = capabilities.missing(role.preferred_capabilities)
        warnings = tuple(
            [
                (
                    f"Preferred capabilities unavailable on profile "
                    f"'{selected.name}': {', '.join(missing)}."
                )
            ]
            if missing
            else []
        )
        reason = None
        if selected.name != role.profile:
            reason = (
                f"Primary profile '{role.profile}' is unavailable; "
                f"using fallback '{selected.name}'."
            )
            warnings = (reason, *warnings)
        return StorageRoleResolution(
            role=role,
            requested_profile=role.profile,
            resolved_profile=selected,
            profiles_tried=candidates[: candidates.index(selected.name) + 1],
            reason=reason,
            warnings=warnings,
            capabilities=capabilities,
        )

    def _unavailable_reason(self, profile_name: str) -> str:
        profile = self._config.profiles.get(profile_name)
        if profile is None:
            return "profile is not configured"
        if not self._factory.is_available(profile.type):
            return f"provider implementation for type '{profile.type}' is unavailable"
        return "profile could not be selected"


class ResolvedRoleStore:
    """Familiar storage operations bound to one resolved role and profile."""

    def __init__(
        self,
        resolution: StorageRoleResolution,
        backend: StorageBackend,
    ) -> None:
        self._resolution = resolution
        self._client = StorageClient(
            backend,
            scope=resolution.role.namespace,
        )

    @property
    def role_name(self) -> str:
        return self._resolution.role.name

    @property
    def profile_name(self) -> str:
        return self._resolution.resolved_profile.name

    @property
    def requested_profile_name(self) -> str:
        return self._resolution.requested_profile

    @property
    def namespace(self) -> str:
        return self._resolution.role.namespace

    @property
    def capabilities(self) -> StorageCapabilities:
        return self._resolution.capabilities

    @property
    def backend_name(self) -> str:
        return self._client.backend_name

    @property
    def warnings(self) -> tuple[str, ...]:
        return self._resolution.warnings

    @property
    def resolution_reason(self) -> str | None:
        return self._resolution.reason

    def put_bytes(
        self,
        key: str,
        content: bytes,
        *,
        media_type: str = "application/octet-stream",
    ) -> StoredObject:
        return self._with_runtime_uri(
            self._client.put_bytes(key, content, media_type=media_type)
        )

    def put_json(self, key: str, value: Any) -> StoredObject:
        return self._with_runtime_uri(self._client.put_json(key, value))

    def put_json_idempotent(self, key: str, value: Any) -> StoredObject:
        return self._with_runtime_uri(
            self._client.put_json_idempotent(key, value)
        )

    def put_stream(
        self,
        key: str,
        source: BinaryIO,
        *,
        media_type: str = "application/octet-stream",
    ) -> StoredObject:
        return self._with_runtime_uri(
            self._client.put_stream(key, source, media_type=media_type)
        )

    def put_file(
        self,
        key: str,
        source: str | Path,
        *,
        media_type: str | None = None,
    ) -> StoredObject:
        return self._with_runtime_uri(
            self._client.put_file(key, source, media_type=media_type)
        )

    def put_directory(self, key: str, source: str | Path) -> StoredObject:
        return self._with_runtime_uri(self._client.put_directory(key, source))

    def open(self, key: str) -> BinaryIO:
        return self._client.open(key)

    def materialize(self, key: str) -> Path:
        return self._client.materialize(key)

    def resolve_local_path(self, key: str) -> Path | None:
        return self._client.resolve_local_path(key)

    def native_path(self, key: str) -> Path:
        return self._client.native_path(key)

    def uri(self, key: str) -> str:
        normalized = validate_storage_key(key)
        logical_key = f"{self.namespace}/{normalized}"
        return f"storage://{self.profile_name}/{logical_key}"

    def stat(self, key: str) -> StoredObject:
        return self._with_runtime_uri(self._client.stat(key))

    def exists(self, key: str) -> bool:
        return self._client.exists(key)

    def list(self, prefix: str = "") -> tuple[StoredObject, ...]:
        return tuple(
            self._with_runtime_uri(item) for item in self._client.list(prefix)
        )

    def delete(self, key: str, *, recursive: bool = False) -> None:
        self._client.delete(key, recursive=recursive)

    def describe(self) -> dict[str, Any]:
        result = self._resolution.to_dict()
        result["backend_name"] = self.backend_name
        return result

    def _with_runtime_uri(self, item: StoredObject) -> StoredObject:
        return replace(
            item,
            uri=f"storage://{self.profile_name}/{item.key}",
        )


class StorageRuntime:
    """Service-facing role router and backend lifecycle owner."""

    def __init__(
        self,
        config: StorageConfig,
        *,
        factory: StorageBackendFactory,
    ) -> None:
        self.config = config
        self._factory = factory
        self._resolver = StorageRoleResolver(config, factory)
        self._backends: dict[str, StorageBackend] = {}

    @classmethod
    def load(
        cls,
        *,
        config_file: str | Path | None = None,
        cwd: str | Path | None = None,
        user_config_file: str | Path | None = None,
        factory: StorageBackendFactory | None = None,
    ) -> "StorageRuntime":
        config = StorageConfig.load(
            config_file=config_file,
            cwd=cwd,
            user_config_file=user_config_file,
        )
        return cls.from_config(config, factory=factory)

    @classmethod
    def from_config(
        cls,
        config: StorageConfig,
        *,
        factory: StorageBackendFactory | None = None,
    ) -> "StorageRuntime":
        selected_factory = factory or default_backend_factory()
        report = config.validate(factory=selected_factory)
        if report.errors:
            messages = "; ".join(issue.message for issue in report.errors)
            raise StorageConfigurationError(
                f"Storage configuration has structural errors: {messages}"
            )
        return cls(config, factory=selected_factory)

    def for_role(self, role_name: str) -> ResolvedRoleStore:
        resolution = self._resolver.resolve(role_name)
        profile = resolution.resolved_profile
        backend = self._backend_for_profile(profile.name)
        return ResolvedRoleStore(resolution, backend)

    def for_profile(
        self, profile_name: str, *, role_name: str = "source_asset"
    ) -> ResolvedRoleStore:
        """Bind one configured profile to a role namespace for audit/GC work."""
        role = self.config.roles.get(role_name)
        profile = self.config.profiles.get(profile_name)
        if role is None or profile is None:
            raise StorageRoleNotFoundError(
                f"Storage profile or role is not configured: {profile_name}/{role_name}"
            )
        resolution = StorageRoleResolution(
            role=role,
            requested_profile=profile_name,
            resolved_profile=profile,
            profiles_tried=(profile_name,),
            reason=None,
            warnings=(),
            capabilities=self._factory.capabilities(profile.type),
        )
        backend = self._backend_for_profile(profile.name)
        return ResolvedRoleStore(resolution, backend)

    def locate(self, uri: str) -> StorageLocation:
        """Resolve one storage URI to provider and file-level metadata."""
        profile_name, storage_key = self._parse_storage_uri(uri)
        backend = self._backend_for_profile(profile_name)
        client = StorageClient(backend)

        exists = client.exists(storage_key)
        local_path: str | None = None
        size_bytes: int | None = None
        if exists:
            existing = client.resolve_local_path(storage_key)
            if existing is not None:
                local_path = str(existing)
            try:
                size_bytes = client.stat(storage_key).size_bytes
            except ObjectNotFoundError:
                exists = False
                local_path = None

        return StorageLocation(
            uri=f"storage://{profile_name}/{storage_key}",
            backend_name=client.backend_name,
            profile_name=profile_name,
            role_name=self._infer_role_from_key(storage_key),
            local_path=local_path,
            exists=exists,
            size_bytes=size_bytes,
        )

    def blobs(self, role_name: str) -> BlobStore:
        """Return immutable Blob/CAS operations bound to a configured role."""
        store = self.for_role(role_name)
        role = self.config.roles[role_name]
        return BlobStore(self, store, dedup_scope=role.dedup_scope)

    def blob_gc(self, role_name: str = "source_asset"):
        from cognityx_storage.blob_gc import BlobGarbageCollector

        return BlobGarbageCollector(self, role_name=role_name)

    def open_blob(self, blob_ref: BlobRef) -> BinaryIO:
        """Open a durable BlobRef through the profile recorded at creation."""
        return self._client_for_blob(blob_ref).open(blob_ref.storage_key)

    def blob_exists(self, blob_ref: BlobRef) -> bool:
        """Check a durable BlobRef without re-resolving its current role."""
        return self._client_for_blob(blob_ref).exists(blob_ref.storage_key)

    def resolve_blob_local_path(self, blob_ref: BlobRef) -> Path | None:
        """Resolve an existing Blob through its recorded profile identity."""
        return self._client_for_blob(blob_ref).resolve_local_path(
            blob_ref.storage_key
        )

    def describe(self) -> dict[str, Any]:
        configuration = self.config.describe(factory=self._factory)
        roles: list[dict[str, Any]] = []
        for role_name, role in self.config.roles.items():
            try:
                roles.append(self._resolver.resolve(role_name).to_dict())
            except StorageRoleUnavailableError as exc:
                roles.append(
                    {
                        "role": role_name,
                        "requested_profile": role.profile,
                        "resolved_profile": None,
                        "fallback_profiles": list(role.fallback_profiles),
                        "namespace": role.namespace,
                        "dedup_scope": role.dedup_scope,
                        "reason": str(exc),
                        "warnings": [str(exc)],
                        "capabilities": None,
                    }
                )
        configuration["resolved_roles"] = roles
        return configuration

    def _client_for_blob(self, blob_ref: BlobRef) -> StorageClient:
        profile = self.config.profiles.get(blob_ref.profile_name)
        if profile is None:
            raise StorageProviderUnavailableError(
                f"Blob profile is not configured: {blob_ref.profile_name}"
            )
        if not self._factory.is_available(profile.type):
            raise StorageProviderUnavailableError(
                f"Blob profile '{profile.name}' provider implementation is unavailable."
            )
        backend = self._backends.get(profile.name)
        if backend is None:
            backend = self._factory.build(profile)
            self._backends[profile.name] = backend
        return StorageClient(backend)

    def _backend_for_profile(self, profile_name: str) -> StorageBackend:
        profile = self.config.profiles.get(profile_name)
        if profile is None:
            raise StorageRoleNotFoundError(
                f"Storage profile is not configured: {profile_name}"
            )
        if not self._factory.is_available(profile.type):
            raise StorageProviderUnavailableError(
                f"Storage profile '{profile.name}' provider is unavailable."
            )
        backend = self._backends.get(profile.name)
        if backend is None:
            backend = self._factory.build(profile)
            self._backends[profile.name] = backend
        return backend

    @staticmethod
    def _parse_storage_uri(uri: str) -> tuple[str, str]:
        if not isinstance(uri, str):
            raise ValueError("Storage URI must be a string.")
        prefix = "storage://"
        if not uri.startswith(prefix):
            raise ValueError("Storage URI must use storage://<profile>/<logical-key>.")
        remainder = uri[len(prefix) :]
        if "/" not in remainder:
            raise ValueError(
                "Storage URI must include one profile segment and one logical key segment."
            )
        profile_name, storage_key = remainder.split("/", 1)
        if not profile_name or "." in profile_name or "/" in profile_name:
            raise ValueError("Storage URI profile segment is invalid.")
        if not storage_key:
            raise ValueError("Storage URI key segment is invalid.")
        validate_storage_key(storage_key)
        return profile_name, storage_key

    def _infer_role_from_key(self, storage_key: str) -> str | None:
        selected = None
        selected_namespace = ""
        for name, role in self.config.roles.items():
            namespace = role.namespace
            if storage_key == namespace or storage_key.startswith(f"{namespace}/"):
                if len(namespace) > len(selected_namespace):
                    selected = name
                    selected_namespace = namespace
        return selected
