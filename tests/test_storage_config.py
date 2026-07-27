from __future__ import annotations

import json
from pathlib import Path

import pytest

from cognityx_storage import (
    DEFAULT_STORAGE_ROOT,
    StorageBackendFactory,
    StorageCapabilities,
    StorageConfig,
    StorageConfigurationError,
)


def _write_config(path: Path, tenant: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"""
[storage]
default_profile = "local-main"

[storage.profiles.local-main]
type = "filesystem"
root = "/tmp/{tenant}"

[storage.roles.catalog]
profile = "local-main"
namespace = "{tenant}"
""",
        encoding="utf-8",
    )
    return path


def test_zero_config_has_local_profile_and_standard_roles(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("COGNITYX_STORAGE_CONFIG", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "missing-user"))

    config = StorageConfig.load(cwd=tmp_path / "missing-project")

    assert config.source == "built-in"
    assert config.default_profile == "local-main"
    assert config.profiles["local-main"].options["root"] == str(
        DEFAULT_STORAGE_ROOT
    )
    assert set(config.roles) == {
        "catalog",
        "source_asset",
        "artifact",
        "dataset",
        "model",
        "cache",
        "temporary",
    }
    assert config.validate().is_valid


def test_config_file_precedence(tmp_path: Path, monkeypatch) -> None:
    explicit = _write_config(tmp_path / "explicit.toml", "explicit")
    environment = _write_config(tmp_path / "environment.toml", "environment")
    project_root = tmp_path / "project"
    _write_config(project_root / ".cognityx/storage.toml", "project")
    user = _write_config(tmp_path / "user.toml", "user")
    monkeypatch.setenv("COGNITYX_STORAGE_CONFIG", str(environment))

    assert StorageConfig.load(
        config_file=explicit, cwd=project_root, user_config_file=user
    ).roles["catalog"].namespace == "explicit"
    assert StorageConfig.load(
        cwd=project_root, user_config_file=user
    ).roles["catalog"].namespace == "environment"
    monkeypatch.delenv("COGNITYX_STORAGE_CONFIG")
    assert StorageConfig.load(
        cwd=project_root, user_config_file=user
    ).roles["catalog"].namespace == "project"
    assert StorageConfig.load(
        cwd=tmp_path / "empty", user_config_file=user
    ).roles["catalog"].namespace == "user"


def test_profiles_capabilities_and_provider_availability() -> None:
    config = StorageConfig.from_dict(
        {
            "storage": {
                "profiles": {
                    "local-main": {
                        "type": "filesystem",
                        "root": "/tmp/storage",
                    },
                    "primary-object": {
                        "type": "object",
                        "provider": "s3",
                        "bucket": "cognityx",
                    },
                    "enterprise-hdfs": {
                        "type": "hdfs",
                        "endpoint": "hdfs://namenode:8020",
                    },
                },
                "roles": {
                    "source_asset": {
                        "profile": "primary-object",
                        "fallback_profiles": ["local-main"],
                        "namespace": "source-assets",
                    }
                },
            }
        }
    )

    report = config.validate()
    description = config.describe()

    assert config.profiles["local-main"].expected_capabilities.native_path
    assert config.profiles["primary-object"].expected_capabilities.range_read
    assert config.profiles["enterprise-hdfs"].expected_capabilities.distributed
    assert {issue.profile_name for issue in report.warnings} >= {
        "primary-object",
        "enterprise-hdfs",
    }
    availability = {
        item["name"]: item["implementation_available"]
        for item in description["profiles"]
    }
    assert availability == {
        "local-main": True,
        "primary-object": False,
        "enterprise-hdfs": False,
    }


def test_validation_reports_structural_errors_and_nonfatal_warnings() -> None:
    config = StorageConfig.from_dict(
        {
            "storage": {
                "profiles": {
                    "local-main": {"type": "filesystem"},
                    "primary-object": {"type": "object"},
                },
                "roles": {
                    "source_asset": {
                        "profile": "primary-object",
                        "fallback_profiles": ["missing"],
                        "namespace": "../unsafe",
                        "preferred_capabilities": ["not_real"],
                    }
                },
            }
        }
    )

    report = config.validate()
    codes = {issue.code for issue in report.errors}

    assert {
        "filesystem_root_required",
        "unknown_role_profile",
        "invalid_role_namespace",
        "unknown_capability",
    } <= codes
    assert any(
        issue.code == "provider_unavailable" for issue in report.warnings
    )


def test_preferred_capability_mismatch_is_a_warning() -> None:
    config = StorageConfig.from_dict(
        {
            "storage": {
                "profiles": {
                    "local-main": {
                        "type": "filesystem",
                        "root": "/tmp/storage",
                    }
                },
                "roles": {
                    "source_asset": {
                        "profile": "local-main",
                        "namespace": "source-assets",
                        "preferred_capabilities": [
                            "distributed",
                            "object_metadata",
                        ],
                    }
                },
            }
        }
    )

    report = config.validate()

    assert report.is_valid
    assert any(
        issue.code == "preferred_capabilities_missing"
        and "distributed" in issue.message
        and "object_metadata" in issue.message
        for issue in report.warnings
    )


def test_secret_values_are_redacted_from_description() -> None:
    secret = "do-not-print-this"
    config = StorageConfig.from_dict(
        {
            "storage": {
                "profiles": {
                    "primary-object": {
                        "type": "object",
                        "credentials_ref": secret,
                        "nested": {"access_token": secret},
                    }
                },
                "roles": {},
            }
        }
    )

    rendered = json.dumps(config.describe())

    assert secret not in rendered
    assert rendered.count("<redacted>") == 2


def test_invalid_toml_and_table_shapes_fail_clearly(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.toml"
    invalid.write_text("[storage", encoding="utf-8")

    with pytest.raises(StorageConfigurationError, match="Cannot load"):
        StorageConfig.load(config_file=invalid)
    with pytest.raises(StorageConfigurationError, match=r"\[storage\]"):
        StorageConfig.from_dict({})


def test_capability_names_are_explicit_and_factory_is_extensible() -> None:
    capabilities = StorageCapabilities(stream_read=True)
    factory = StorageBackendFactory()
    factory.register("custom", lambda profile: object())

    assert capabilities.supports("stream_read")
    assert not capabilities.supports("distributed")
    with pytest.raises(ValueError, match="Unknown storage capability"):
        capabilities.supports("unknown")
    assert factory.is_available("custom")
