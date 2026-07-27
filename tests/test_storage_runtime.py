from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest

from cognityx_storage import (
    LocalStorageBackend,
    StorageBackendFactory,
    StorageCapabilities,
    StorageClient,
    StorageConfig,
    StorageRoleNotFoundError,
    StorageRoleUnavailableError,
    StorageRuntime,
    UnsupportedOperationError,
)


def _local_runtime(tmp_path: Path) -> StorageRuntime:
    return StorageRuntime.from_config(StorageConfig.built_in(root=tmp_path))


def test_zero_config_role_resolution_and_namespaced_io(tmp_path: Path) -> None:
    runtime = _local_runtime(tmp_path)
    source = tmp_path / "report.txt"
    source.write_text("hello", encoding="utf-8")

    assets = runtime.for_role("source_asset")
    stored = assets.put_file("incoming/report.txt", source)

    assert assets.role_name == "source_asset"
    assert assets.profile_name == "local-main"
    assert assets.namespace == "source-assets"
    assert assets.backend_name == "LocalStorageBackend"
    assert stored.key == "source-assets/incoming/report.txt"
    assert stored.uri == (
        "storage://local-main/source-assets/incoming/report.txt"
    )
    assert assets.uri("incoming/report.txt") == stored.uri
    assert (tmp_path / "source-assets/incoming/report.txt").is_file()
    assert assets.exists("incoming/report.txt")
    assert assets.stat("incoming/report.txt").uri == stored.uri
    assert assets.list("incoming")[0].uri == stored.uri
    with assets.open("incoming/report.txt") as opened:
        assert opened.read() == b"hello"


def test_namespace_is_applied_exactly_once(tmp_path: Path) -> None:
    assets = _local_runtime(tmp_path).for_role("source_asset")

    stored = assets.put_stream("one.bin", BytesIO(b"one"))

    assert stored.key == "source-assets/one.bin"
    assert not (tmp_path / "source-assets/source-assets/one.bin").exists()


def test_runtime_uri_does_not_change_direct_client_uri(tmp_path: Path) -> None:
    direct = StorageClient(LocalStorageBackend(tmp_path)).for_shared_data()
    runtime = _local_runtime(tmp_path)

    assert direct.uri("existing/key") == "storage://shared/existing/key"
    assert runtime.for_role("artifact").uri("new/key") == (
        "storage://local-main/artifacts/new/key"
    )


def test_unavailable_primary_uses_available_fallback(tmp_path: Path) -> None:
    config = StorageConfig.from_dict(
        {
            "storage": {
                "profiles": {
                    "primary-object": {"type": "object", "provider": "s3"},
                    "local-main": {
                        "type": "filesystem",
                        "root": str(tmp_path),
                    },
                },
                "roles": {
                    "source_asset": {
                        "profile": "primary-object",
                        "fallback_profiles": ["local-main"],
                        "namespace": "source-assets",
                        "preferred_capabilities": [
                            "stream_write",
                            "distributed",
                        ],
                    }
                },
            }
        }
    )

    runtime = StorageRuntime.from_config(config)
    assets = runtime.for_role("source_asset")

    assert assets.requested_profile_name == "primary-object"
    assert assets.profile_name == "local-main"
    assert "using fallback 'local-main'" in (assets.resolution_reason or "")
    assert any("distributed" in warning for warning in assets.warnings)
    described = runtime.describe()["resolved_roles"][0]
    assert described["requested_profile"] == "primary-object"
    assert described["resolved_profile"] == "local-main"


def test_unavailable_role_fails_only_when_requested(tmp_path: Path) -> None:
    config = StorageConfig.from_dict(
        {
            "storage": {
                "profiles": {"primary-object": {"type": "object"}},
                "roles": {
                    "source_asset": {
                        "profile": "primary-object",
                        "namespace": "source-assets",
                    }
                },
            }
        }
    )

    runtime = StorageRuntime.from_config(config)
    assert runtime.describe()["resolved_roles"][0]["resolved_profile"] is None
    with pytest.raises(
        StorageRoleUnavailableError,
        match=r"source_asset.*primary-object.*unavailable",
    ):
        runtime.for_role("source_asset")


def test_unknown_role_is_explicit(tmp_path: Path) -> None:
    runtime = _local_runtime(tmp_path)

    with pytest.raises(StorageRoleNotFoundError, match="not configured"):
        runtime.for_role("future-role")


def test_native_path_supports_uncreated_filesystem_target(tmp_path: Path) -> None:
    catalog = _local_runtime(tmp_path).for_role("catalog")

    target = catalog.native_path("ingest/source_catalog.sqlite3")

    assert target == tmp_path / "catalog/ingest/source_catalog.sqlite3"
    assert not target.exists()
    assert not target.parent.exists()


def test_available_remote_backend_does_not_fake_native_path() -> None:
    class RemoteBackend:
        pass

    config = StorageConfig.from_dict(
        {
            "storage": {
                "profiles": {"primary-object": {"type": "object"}},
                "roles": {
                    "artifact": {
                        "profile": "primary-object",
                        "namespace": "artifacts",
                    }
                },
            }
        }
    )
    factory = StorageBackendFactory()
    factory.register(
        "object",
        lambda profile: RemoteBackend(),
        capabilities=StorageCapabilities(
            stream_read=True,
            stream_write=True,
            distributed=True,
        ),
    )
    store = StorageRuntime.from_config(config, factory=factory).for_role(
        "artifact"
    )

    with pytest.raises(UnsupportedOperationError, match="native paths"):
        store.native_path("one.bin")


def test_resolve_local_path_still_requires_existing_content(
    tmp_path: Path,
) -> None:
    artifacts = _local_runtime(tmp_path).for_role("artifact")
    artifacts.put_bytes("result.bin", b"result")

    assert artifacts.resolve_local_path("result.bin") == (
        tmp_path / "artifacts/result.bin"
    )
