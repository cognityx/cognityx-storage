from __future__ import annotations

import json
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
    StorageLocation,
    UnsupportedOperationError,
)


class _RemoteBackend:
    def put_stream(self, *args, **kwargs):
        raise AssertionError("unexpected")

    def put_file(self, *args, **kwargs):
        raise AssertionError("unexpected")

    def put_directory(self, *args, **kwargs):
        raise AssertionError("unexpected")

    def open_reader(self, *args, **kwargs):
        raise AssertionError("unexpected")

    def materialize(self, *args, **kwargs):
        raise AssertionError("unexpected")

    def stat(self, key: str):
        raise AssertionError("unexpected")

    def exists(self, key: str) -> bool:
        return key == "remote/artifacts/manifest.json"

    def list(self, prefix: str = ""):
        raise AssertionError("unexpected")

    def delete(self, *args, **kwargs):
        raise AssertionError("unexpected")


class _SyntheticRemoteBackend:
    def __init__(self) -> None:
        self._objects = {"objects/artifacts/report.json": b"artifact"}

    def put_stream(self, *args, **kwargs):
        raise AssertionError("unexpected")

    def put_file(self, *args, **kwargs):
        raise AssertionError("unexpected")

    def put_directory(self, *args, **kwargs):
        raise AssertionError("unexpected")

    def open_reader(self, *args, **kwargs):
        raise AssertionError("unexpected")

    def materialize(self, *args, **kwargs):
        raise AssertionError("unexpected")

    def stat(self, key: str):
        if key in self._objects:
            return type(
                "_Object",
                (),
                {
                    "uri": f"storage://remote-main/{key}",
                    "size_bytes": len(self._objects[key]),
                },
            )
        raise RuntimeError("missing")

    def exists(self, key: str) -> bool:
        return key in self._objects

    def list(self, prefix: str = ""):
        raise AssertionError("unexpected")

    def delete(self, *args, **kwargs):
        raise AssertionError("unexpected")

    def resolve_local_path(self, key: str):
        return None


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


def test_runtime_locate_reports_existing_local_artifact(
    tmp_path: Path,
) -> None:
    runtime = _local_runtime(tmp_path)
    artifacts = runtime.for_role("artifact")
    stored = artifacts.put_bytes("documents/report.json", b"hello")

    location = runtime.locate(stored.uri)

    assert location == StorageLocation(
        uri=stored.uri,
        backend_name="LocalStorageBackend",
        profile_name="local-main",
        role_name="artifact",
        local_path=str(tmp_path / "artifacts/documents/report.json"),
        exists=True,
        size_bytes=stored.size_bytes,
    )


def test_runtime_locate_handles_missing_artifact_without_errors(
    tmp_path: Path,
) -> None:
    runtime = _local_runtime(tmp_path)

    location = runtime.locate("storage://local-main/artifacts/missing.txt")

    assert location.uri == "storage://local-main/artifacts/missing.txt"
    assert location.exists is False
    assert location.local_path is None
    assert location.size_bytes is None


def test_runtime_locate_infers_role_for_remote_profile_and_no_local_path(
    tmp_path: Path,
) -> None:
    config = StorageConfig.from_dict(
        {
            "storage": {
                "profiles": {
                    "remote-main": {"type": "object", "provider": "mock"},
                },
                "roles": {
                    "artifact": {
                        "profile": "remote-main",
                        "namespace": "artifacts",
                    }
                },
            }
        }
    )
    factory = StorageBackendFactory()
    factory.register("object", lambda profile: _RemoteBackend())
    runtime = StorageRuntime.from_config(config, factory=factory)

    location = runtime.locate("storage://remote-main/artifacts/index.json")

    assert isinstance(location, StorageLocation)
    assert location.backend_name == "_RemoteBackend"
    assert location.profile_name == "remote-main"
    assert location.role_name == "artifact"
    assert location.exists is False
    assert location.local_path is None


def test_runtime_locate_with_synthetic_non_local_backend_reports_size_when_available(
    tmp_path: Path,
) -> None:
    config = StorageConfig.from_dict(
        {
            "storage": {
                "profiles": {
                    "remote-main": {"type": "object", "provider": "mock"},
                },
                "roles": {
                    "artifact": {
                        "profile": "remote-main",
                        "namespace": "objects",
                    }
                },
            }
        }
    )
    factory = StorageBackendFactory()
    factory.register(
        "object",
        lambda profile: _SyntheticRemoteBackend(),
        capabilities=StorageCapabilities(
            stream_read=True,
            stream_write=True,
            distributed=True,
        ),
    )
    runtime = StorageRuntime.from_config(config, factory=factory)

    present = runtime.locate("storage://remote-main/objects/artifacts/report.json")
    missing = runtime.locate("storage://remote-main/objects/missing/report.json")

    assert present == StorageLocation(
        uri="storage://remote-main/objects/artifacts/report.json",
        backend_name="_SyntheticRemoteBackend",
        profile_name="remote-main",
        role_name="artifact",
        local_path=None,
        exists=True,
        size_bytes=len(b"artifact"),
    )
    assert missing.exists is False
    assert missing.size_bytes is None
    assert missing.local_path is None


def test_runtime_locate_supports_profile_and_role_routing_without_cross_profile_leak(
    tmp_path: Path,
) -> None:
    config = StorageConfig.from_dict(
        {
            "storage": {
                "profiles": {
                    "local-main": {
                        "type": "filesystem",
                        "root": str(tmp_path / "primary"),
                    },
                    "remote-main": {
                        "type": "object",
                        "provider": "mock",
                    },
                },
                "roles": {
                    "artifact": {
                        "profile": "local-main",
                        "namespace": "artifacts",
                    },
                    "source_asset": {
                        "profile": "remote-main",
                        "namespace": "source-assets",
                    },
                },
            }
        }
    )
    factory = StorageBackendFactory()
    factory.register(
        "filesystem",
        lambda profile: LocalStorageBackend(profile.options["root"]),
        capabilities=StorageCapabilities(
            stream_read=True,
            stream_write=True,
            distributed=False,
        ),
    )
    factory.register(
        "object",
        lambda profile: _RemoteBackend(),
        capabilities=StorageCapabilities(
            stream_read=True,
            stream_write=True,
            distributed=True,
        ),
    )
    runtime = StorageRuntime.from_config(config, factory=factory)

    local = runtime.for_role("artifact").put_bytes("report.json", b"artifact")
    local_asset_profile = runtime.for_profile("remote-main", role_name="source_asset")
    assert local_asset_profile.exists("report.pdf") is False

    artifact_location = runtime.locate(local.uri)
    missing_role = runtime.locate("storage://local-main/source-assets/report.pdf")
    remote_location = runtime.locate("storage://remote-main/source-assets/report.pdf")

    assert artifact_location.role_name == "artifact"
    assert artifact_location.profile_name == "local-main"
    assert remote_location.role_name == "source_asset"
    assert remote_location.profile_name == "remote-main"
    assert remote_location.local_path is None
    assert missing_role.exists is False


def test_runtime_locate_preserves_tenant_scope_without_cross_context_leak(
    tmp_path: Path,
) -> None:
    config = StorageConfig.from_dict(
        {
            "storage": {
                "profiles": {
                    "local-main": {
                        "type": "filesystem",
                        "root": str(tmp_path / "primary"),
                    },
                },
                "roles": {
                    "artifact": {
                        "profile": "local-main",
                        "namespace": "artifacts",
                    },
                },
            }
        }
    )
    runtime = StorageRuntime.from_config(config)

    runtime.for_profile("local-main", role_name="artifact").put_bytes(
        "tenant-acme/reports/overview.json",
        b"tenant-a",
    )
    runtime.for_profile("local-main", role_name="artifact").put_bytes(
        "tenant-omega/reports/overview.json",
        b"tenant-b",
    )

    tenant_a_location = runtime.locate(
        "storage://local-main/artifacts/tenant-acme/reports/overview.json"
    )
    tenant_b_location = runtime.locate(
        "storage://local-main/artifacts/tenant-omega/reports/overview.json"
    )
    missing_tenant_location = runtime.locate(
        "storage://local-main/artifacts/tenant-missing/reports/overview.json"
    )

    assert tenant_a_location.exists
    assert tenant_b_location.exists
    assert missing_tenant_location.exists is False
    assert tenant_a_location.uri != tenant_b_location.uri


def test_runtime_locate_to_dict_is_json_safe_and_does_not_expose_secrets(
    tmp_path: Path,
) -> None:
    runtime = _local_runtime(tmp_path)
    stored = runtime.for_role("artifact").put_bytes("documents/report.json", b"hello")

    payload = runtime.locate(stored.uri).to_dict()
    encoded = json.dumps(payload, sort_keys=True)

    assert stored.uri in encoded
    assert payload["backend"] == "LocalStorageBackend"
    assert "local_path" in payload
    assert "storage://" in encoded
    assert "secret" not in encoded.lower()
