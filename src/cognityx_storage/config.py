"""Storage profiles, roles, TOML discovery, and validation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import os
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any
import tomllib

from cognityx_storage.capabilities import (
    StorageCapabilities,
    expected_capabilities,
    known_profile_types,
)
from cognityx_storage.exceptions import (
    InvalidStorageKeyError,
    StorageConfigurationError,
)
from cognityx_storage.local import DEFAULT_STORAGE_ROOT, validate_storage_key

_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_DEFAULT_ROLE_NAMESPACES = {
    "catalog": "catalog",
    "source_asset": "source-assets",
    "artifact": "artifacts",
    "dataset": "datasets",
    "model": "models",
    "cache": "cache",
    "temporary": "temporary",
}
_DEDUP_SCOPES = frozenset({"none", "context", "tenant", "platform"})
_SECRET_MARKERS = ("credential", "secret", "password", "token")


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_freeze(item) for item in value)
    return value


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value


def _redacted_options(options: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in options.items():
        lowered = key.lower()
        key_parts = set(re.split(r"[^a-z0-9]+", lowered))
        if any(marker in lowered for marker in _SECRET_MARKERS) or "key" in key_parts:
            result[key] = "<redacted>"
        elif isinstance(value, Mapping):
            result[key] = _redacted_options(value)
        elif isinstance(value, tuple):
            result[key] = [
                _redacted_options(item) if isinstance(item, Mapping) else _plain(item)
                for item in value
            ]
        else:
            result[key] = value
    return result


@dataclass(frozen=True, slots=True)
class StorageProfile:
    """One configured physical or logical storage system."""

    name: str
    type: str
    options: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "options", _freeze(self.options))

    @property
    def expected_capabilities(self) -> StorageCapabilities:
        """Return the declared signature for this profile type."""
        return expected_capabilities(self.type)


@dataclass(frozen=True, slots=True)
class StorageRole:
    """Describe what Cognityx wants to use storage for."""

    name: str
    profile: str | None
    fallback_profiles: tuple[str, ...] = ()
    namespace: str = ""
    preferred_capabilities: tuple[str, ...] = ()
    dedup_scope: str = "tenant"


@dataclass(frozen=True, slots=True)
class StorageValidationIssue:
    """One structural error or non-fatal configuration warning."""

    severity: str
    code: str
    message: str
    profile_name: str | None = None
    role_name: str | None = None

    def to_dict(self) -> dict[str, str]:
        result = {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
        }
        if self.profile_name is not None:
            result["profile_name"] = self.profile_name
        if self.role_name is not None:
            result["role_name"] = self.role_name
        return result


@dataclass(frozen=True, slots=True)
class StorageValidationReport:
    """Structured validation result for operator tooling."""

    issues: tuple[StorageValidationIssue, ...] = ()

    @property
    def errors(self) -> tuple[StorageValidationIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "error")

    @property
    def warnings(self) -> tuple[StorageValidationIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "warning")

    @property
    def is_valid(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.is_valid,
            "errors": [issue.to_dict() for issue in self.errors],
            "warnings": [issue.to_dict() for issue in self.warnings],
        }


@dataclass(frozen=True, slots=True)
class StorageConfig:
    """Selected storage configuration before runtime role resolution."""

    profiles: Mapping[str, StorageProfile]
    roles: Mapping[str, StorageRole]
    default_profile: str | None = None
    source: str = "built-in"

    def __post_init__(self) -> None:
        object.__setattr__(self, "profiles", MappingProxyType(dict(self.profiles)))
        object.__setattr__(self, "roles", MappingProxyType(dict(self.roles)))

    @classmethod
    def load(
        cls,
        *,
        config_file: str | Path | None = None,
        cwd: str | Path | None = None,
        user_config_file: str | Path | None = None,
    ) -> "StorageConfig":
        """Select one TOML file by precedence or return the built-in local config."""
        selected = _select_config_file(
            config_file,
            cwd=cwd,
            user_config_file=user_config_file,
        )
        if selected is None:
            return cls.built_in()
        try:
            payload = tomllib.loads(selected.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
            raise StorageConfigurationError(
                f"Cannot load storage configuration '{selected}': {exc}"
            ) from exc
        return cls.from_dict(payload, source=str(selected))

    @classmethod
    def built_in(
        cls,
        *,
        root: str | Path = DEFAULT_STORAGE_ROOT,
    ) -> "StorageConfig":
        """Return the zero-config filesystem profile and standard Cognityx roles."""
        profile = StorageProfile(
            name="local-main",
            type="filesystem",
            options={"root": str(root)},
        )
        roles = {
            name: StorageRole(
                name=name,
                profile="local-main",
                namespace=namespace,
                dedup_scope="none" if name == "temporary" else "tenant",
                preferred_capabilities=(
                    "native_path",
                    "random_write",
                    "file_locking",
                )
                if name == "catalog"
                else (),
            )
            for name, namespace in _DEFAULT_ROLE_NAMESPACES.items()
        }
        return cls(
            profiles={profile.name: profile},
            roles=roles,
            default_profile=profile.name,
        )

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
        *,
        source: str = "<mapping>",
    ) -> "StorageConfig":
        """Parse one already-selected TOML-style mapping."""
        storage = payload.get("storage")
        if not isinstance(storage, Mapping):
            raise StorageConfigurationError(
                "Storage configuration must contain a [storage] table."
            )
        raw_profiles = storage.get("profiles", {})
        raw_roles = storage.get("roles", {})
        if not isinstance(raw_profiles, Mapping) or not isinstance(raw_roles, Mapping):
            raise StorageConfigurationError(
                "storage.profiles and storage.roles must be TOML tables."
            )

        profiles: dict[str, StorageProfile] = {}
        for raw_name, raw_profile in raw_profiles.items():
            if not isinstance(raw_profile, Mapping):
                raise StorageConfigurationError(
                    f"Storage profile '{raw_name}' must be a TOML table."
                )
            values = dict(raw_profile)
            profile_type = values.pop("type", "")
            if not isinstance(profile_type, str):
                raise StorageConfigurationError(
                    f"Storage profile '{raw_name}' type must be a string."
                )
            profiles[str(raw_name)] = StorageProfile(
                name=str(raw_name),
                type=profile_type,
                options=values,
            )

        default_profile = storage.get("default_profile")
        if default_profile is not None and not isinstance(default_profile, str):
            raise StorageConfigurationError("storage.default_profile must be a string.")

        roles: dict[str, StorageRole] = {}
        for raw_name, raw_role in raw_roles.items():
            if not isinstance(raw_role, Mapping):
                raise StorageConfigurationError(
                    f"Storage role '{raw_name}' must be a TOML table."
                )
            role_name = str(raw_name)
            profile = raw_role.get("profile", default_profile)
            fallbacks = raw_role.get("fallback_profiles", [])
            preferred = raw_role.get("preferred_capabilities", [])
            namespace = raw_role.get("namespace", "")
            dedup_scope = raw_role.get("dedup_scope", "tenant")
            if profile is not None and not isinstance(profile, str):
                raise StorageConfigurationError(
                    f"Storage role '{role_name}' profile must be a string."
                )
            if not isinstance(fallbacks, list) or not all(
                isinstance(item, str) for item in fallbacks
            ):
                raise StorageConfigurationError(
                    f"Storage role '{role_name}' fallback_profiles must be strings."
                )
            if not isinstance(preferred, list) or not all(
                isinstance(item, str) for item in preferred
            ):
                raise StorageConfigurationError(
                    f"Storage role '{role_name}' preferred_capabilities must be strings."
                )
            if not isinstance(namespace, str):
                raise StorageConfigurationError(
                    f"Storage role '{role_name}' namespace must be a string."
                )
            if not isinstance(dedup_scope, str):
                raise StorageConfigurationError(
                    f"Storage role '{role_name}' dedup_scope must be a string."
                )
            roles[role_name] = StorageRole(
                name=role_name,
                profile=profile,
                fallback_profiles=tuple(fallbacks),
                namespace=namespace,
                preferred_capabilities=tuple(preferred),
                dedup_scope=dedup_scope,
            )

        return cls(
            profiles=profiles,
            roles=roles,
            default_profile=default_profile,
            source=source,
        )

    def validate(self, *, factory: Any | None = None) -> StorageValidationReport:
        """Report structural errors and non-fatal provider/capability warnings."""
        if factory is None:
            from cognityx_storage.factory import default_backend_factory

            factory = default_backend_factory()
        issues: list[StorageValidationIssue] = []

        if self.default_profile is not None and self.default_profile not in self.profiles:
            issues.append(
                StorageValidationIssue(
                    "error",
                    "unknown_default_profile",
                    f"Default profile '{self.default_profile}' is not configured.",
                    profile_name=self.default_profile,
                )
            )

        for profile in self.profiles.values():
            if not _NAME_PATTERN.fullmatch(profile.name):
                issues.append(
                    StorageValidationIssue(
                        "error",
                        "invalid_profile_name",
                        f"Invalid storage profile name: '{profile.name}'.",
                        profile_name=profile.name,
                    )
                )
            if profile.type not in known_profile_types():
                issues.append(
                    StorageValidationIssue(
                        "error",
                        "unknown_profile_type",
                        f"Storage profile '{profile.name}' has unsupported type "
                        f"'{profile.type}'.",
                        profile_name=profile.name,
                    )
                )
                continue
            if profile.type == "filesystem":
                root = profile.options.get("root")
                if not isinstance(root, str) or not root.strip():
                    issues.append(
                        StorageValidationIssue(
                            "error",
                            "filesystem_root_required",
                            f"Filesystem profile '{profile.name}' requires a root.",
                            profile_name=profile.name,
                        )
                    )
            if not factory.is_available(profile.type):
                issues.append(
                    StorageValidationIssue(
                        "warning",
                        "provider_unavailable",
                        f"Storage provider implementation is unavailable for profile "
                        f"'{profile.name}' (type '{profile.type}').",
                        profile_name=profile.name,
                    )
                )

        capability_names = StorageCapabilities.names()
        for role in self.roles.values():
            if not _NAME_PATTERN.fullmatch(role.name):
                issues.append(
                    StorageValidationIssue(
                        "error",
                        "invalid_role_name",
                        f"Invalid storage role name: '{role.name}'.",
                        role_name=role.name,
                    )
                )
            try:
                validate_storage_key(role.namespace)
            except InvalidStorageKeyError as exc:
                issues.append(
                    StorageValidationIssue(
                        "error",
                        "invalid_role_namespace",
                        f"Storage role '{role.name}' has an invalid namespace: {exc}",
                        role_name=role.name,
                    )
                )
            candidates = tuple(
                name
                for name in (role.profile, *role.fallback_profiles)
                if name is not None
            )
            if role.profile is None:
                issues.append(
                    StorageValidationIssue(
                        "error",
                        "role_profile_required",
                        f"Storage role '{role.name}' has no profile.",
                        role_name=role.name,
                    )
                )
            for name in candidates:
                if name not in self.profiles:
                    issues.append(
                        StorageValidationIssue(
                            "error",
                            "unknown_role_profile",
                            f"Storage role '{role.name}' references unknown profile "
                            f"'{name}'.",
                            profile_name=name,
                            role_name=role.name,
                        )
                    )
            unknown_capabilities = sorted(
                set(role.preferred_capabilities) - capability_names
            )
            if role.dedup_scope not in _DEDUP_SCOPES:
                issues.append(
                    StorageValidationIssue(
                        "error",
                        "invalid_dedup_scope",
                        f"Storage role '{role.name}' has invalid dedup_scope "
                        f"'{role.dedup_scope}'. Expected one of: "
                        f"{', '.join(sorted(_DEDUP_SCOPES))}.",
                        role_name=role.name,
                    )
                )
            for capability in unknown_capabilities:
                issues.append(
                    StorageValidationIssue(
                        "error",
                        "unknown_capability",
                        f"Storage role '{role.name}' requests unknown capability "
                        f"'{capability}'.",
                        role_name=role.name,
                    )
                )

            resolved = next(
                (
                    self.profiles[name]
                    for name in candidates
                    if name in self.profiles
                    and factory.is_available(self.profiles[name].type)
                ),
                None,
            )
            if resolved is None and candidates:
                issues.append(
                    StorageValidationIssue(
                        "warning",
                        "role_unavailable",
                        f"Storage role '{role.name}' has no profile with an available "
                        "provider implementation.",
                        role_name=role.name,
                    )
                )
            elif resolved is not None and not unknown_capabilities:
                missing = factory.capabilities(resolved.type).missing(
                    role.preferred_capabilities
                )
                if missing:
                    issues.append(
                        StorageValidationIssue(
                            "warning",
                            "preferred_capabilities_missing",
                            f"Storage role '{role.name}' resolves to profile "
                            f"'{resolved.name}' without preferred capabilities: "
                            f"{', '.join(missing)}.",
                            profile_name=resolved.name,
                            role_name=role.name,
                        )
                    )

        return StorageValidationReport(tuple(issues))

    def describe(self, *, factory: Any | None = None) -> dict[str, Any]:
        """Return secret-safe structured configuration diagnostics."""
        if factory is None:
            from cognityx_storage.factory import default_backend_factory

            factory = default_backend_factory()
        report = self.validate(factory=factory)
        return {
            "source": self.source,
            "default_profile": self.default_profile,
            "profiles": [
                {
                    "name": profile.name,
                    "type": profile.type,
                    "implementation_available": factory.is_available(profile.type),
                    "expected_capabilities": profile.expected_capabilities.to_dict(),
                    "available_capabilities": (
                        factory.capabilities(profile.type).to_dict()
                        if factory.is_available(profile.type)
                        else None
                    ),
                    "options": _redacted_options(profile.options),
                }
                for profile in self.profiles.values()
            ],
            "roles": [
                {
                    "name": role.name,
                    "profile": role.profile,
                    "fallback_profiles": list(role.fallback_profiles),
                    "namespace": role.namespace,
                    "preferred_capabilities": list(role.preferred_capabilities),
                    "dedup_scope": role.dedup_scope,
                }
                for role in self.roles.values()
            ],
            "validation": report.to_dict(),
        }


def _select_config_file(
    explicit: str | Path | None,
    *,
    cwd: str | Path | None,
    user_config_file: str | Path | None,
) -> Path | None:
    if explicit is not None:
        path = Path(explicit)
        if not path.is_file():
            raise FileNotFoundError(f"Storage config file does not exist: {path}")
        return path
    if configured := os.environ.get("COGNITYX_STORAGE_CONFIG"):
        path = Path(configured)
        if not path.is_file():
            raise FileNotFoundError(
                f"COGNITYX_STORAGE_CONFIG does not exist: {path}"
            )
        return path
    project = Path(cwd or Path.cwd()) / ".cognityx" / "storage.toml"
    if project.is_file():
        return project
    user = (
        Path(user_config_file)
        if user_config_file is not None
        else Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        / "cognityx"
        / "storage.toml"
    )
    return user if user.is_file() else None
