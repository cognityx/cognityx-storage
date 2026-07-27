from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path

import pytest

from cognityx_resource import ResourceContext
from cognityx_storage import (
    BlobStore,
    ObjectConsistencyError,
    StorageConfig,
    StorageRuntime,
    build_cas_key,
    resolve_dedup_domain,
)


def _config(
    root: Path,
    *,
    source_scope: str = "tenant",
    dataset_scope: str = "tenant",
    source_profile: str = "local-main",
    fallback_profiles: list[str] | None = None,
    extra_profiles: dict | None = None,
) -> StorageConfig:
    profiles = {
        "local-main": {"type": "filesystem", "root": str(root)},
        **(extra_profiles or {}),
    }
    return StorageConfig.from_dict(
        {
            "storage": {
                "profiles": profiles,
                "roles": {
                    "source_asset": {
                        "profile": source_profile,
                        "fallback_profiles": fallback_profiles or [],
                        "namespace": "source-assets",
                        "dedup_scope": source_scope,
                    },
                    "dataset": {
                        "profile": "local-main",
                        "namespace": "datasets",
                        "dedup_scope": dataset_scope,
                    },
                },
            }
        }
    )


def _runtime(root: Path, **kwargs) -> StorageRuntime:
    return StorageRuntime.from_config(_config(root, **kwargs))


def _context(
    *,
    tenant: str | None = "acme",
    principal: str | None = "alice",
    project: str | None = None,
    context_type: str = "user",
) -> ResourceContext:
    return ResourceContext(
        context_type=context_type,
        tenant_id=tenant,
        principal_id=principal,
        project_id=project,
    )


def _physical_blobs(root: Path) -> list[Path]:
    return [
        path
        for path in root.rglob("*")
        if path.is_file() and "blob-domains" in path.parts
    ]


def test_tenant_dedup_reuses_bytes_without_exposing_a_hit(tmp_path: Path) -> None:
    blobs = _runtime(tmp_path).blobs("source_asset")
    alice = _context(principal="alice", project="project-a")
    bob = _context(principal="bob", project="project-b")

    first = blobs.put_bytes(b"same", context=alice, media_type="text/plain")
    second = blobs.put_bytes(
        b"same", context=bob, media_type="application/octet-stream"
    )

    assert first.blob_id == second.blob_id
    assert first.uri == second.uri
    assert first.digest == second.digest
    assert first.media_type != second.media_type
    assert len(_physical_blobs(tmp_path)) == 1
    assert set(first.to_dict()) == {
        "blob_id",
        "role_name",
        "profile_name",
        "uri",
        "storage_key",
        "algorithm",
        "digest",
        "dedup_domain_id",
        "size_bytes",
        "media_type",
    }


def test_put_file_hashes_and_publishes_large_content_incrementally(
    tmp_path: Path,
) -> None:
    source = tmp_path / "report.pdf"
    content = b"pdf-content-" * (1024 * 256)
    source.write_bytes(content)
    runtime = _runtime(tmp_path / "storage")

    reference = runtime.blobs("source_asset").put_file(
        source,
        context=_context(),
    )

    assert reference.digest == sha256(content).hexdigest()
    assert reference.size_bytes == len(content)
    assert reference.media_type == "application/pdf"
    assert reference.storage_key.endswith(reference.digest)
    with runtime.open_blob(reference) as opened:
        assert opened.read() == content


def test_put_file_publishes_captured_snapshot_when_source_mutates(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "report.pdf"
    captured = b"captured snapshot"
    replacement = b"caller changed the original"
    source.write_bytes(captured)
    runtime = _runtime(tmp_path / "storage")
    blobs = runtime.blobs("source_asset")
    original_publish = BlobStore._publish

    def mutate_source_before_publish(self, staged_path, **kwargs):
        source.write_bytes(replacement)
        return original_publish(self, staged_path, **kwargs)

    monkeypatch.setattr(BlobStore, "_publish", mutate_source_before_publish)

    reference = blobs.put_file(source, context=_context())

    with runtime.open_blob(reference) as opened:
        stored = opened.read()
    assert source.read_bytes() == replacement
    assert stored == captured
    assert sha256(stored).hexdigest() == reference.digest
    assert len(stored) == reference.size_bytes


def test_put_file_staging_cleanup_on_success(
    tmp_path: Path,
) -> None:
    source = tmp_path / "report.pdf"
    source.write_bytes(b"content")
    spool = tmp_path / "spool"
    spool.mkdir()
    runtime = _runtime(tmp_path / "storage")
    blobs = BlobStore(
        runtime,
        runtime.for_role("source_asset"),
        spool_directory=spool,
    )

    blobs.put_file(source, context=_context())

    assert list(spool.iterdir()) == []


def test_put_file_staging_cleanup_on_publication_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "report.pdf"
    source.write_bytes(b"content")
    spool = tmp_path / "spool"
    spool.mkdir()
    runtime = _runtime(tmp_path / "storage")
    role_store = runtime.for_role("source_asset")
    blobs = BlobStore(
        runtime,
        role_store,
        spool_directory=spool,
    )

    def fail_publication(*args, **kwargs):
        raise RuntimeError("publication failed")

    monkeypatch.setattr(role_store, "put_file", fail_publication)

    with pytest.raises(RuntimeError, match="publication failed"):
        blobs.put_file(source, context=_context())
    assert list(spool.iterdir()) == []


def test_tenants_are_physically_isolated(tmp_path: Path) -> None:
    blobs = _runtime(tmp_path).blobs("source_asset")

    first = blobs.put_bytes(b"same", context=_context(tenant="tenant-a"))
    second = blobs.put_bytes(b"same", context=_context(tenant="tenant-b"))

    assert first.dedup_domain_id != second.dedup_domain_id
    assert first.blob_id != second.blob_id
    assert first.uri != second.uri
    assert len(_physical_blobs(tmp_path)) == 2
    assert "tenant-a" not in first.storage_key
    assert "tenant-b" not in second.storage_key


def test_tenantless_principals_are_isolated(tmp_path: Path) -> None:
    blobs = _runtime(tmp_path).blobs("source_asset")

    alice = blobs.put_bytes(
        b"same", context=_context(tenant=None, principal="alice")
    )
    bob = blobs.put_bytes(
        b"same", context=_context(tenant=None, principal="bob")
    )

    assert alice.dedup_domain_id.startswith("principal-")
    assert bob.dedup_domain_id.startswith("principal-")
    assert alice.blob_id != bob.blob_id


def test_system_context_is_separate_and_service_identity_is_stable(
    tmp_path: Path,
) -> None:
    blobs = _runtime(tmp_path).blobs("source_asset")
    system = _context(
        tenant="acme",
        principal="service:policy-sync",
        context_type="system",
    )
    user = _context(tenant="acme", principal="alice")

    first_system = blobs.put_bytes(b"same", context=system)
    second_system = blobs.put_bytes(b"same", context=system)
    user_blob = blobs.put_bytes(b"same", context=user)

    assert first_system.dedup_domain_id.startswith("system-")
    assert first_system.blob_id == second_system.blob_id
    assert first_system.blob_id != user_blob.blob_id


def test_context_scope_reuses_only_the_same_resource_context(
    tmp_path: Path,
) -> None:
    blobs = _runtime(tmp_path, source_scope="context").blobs("source_asset")
    first_context = _context(project="one")
    second_context = _context(project="two")

    first = blobs.put_bytes(b"same", context=first_context)
    repeated = blobs.put_bytes(b"same", context=first_context)
    second = blobs.put_bytes(b"same", context=second_context)

    assert first.blob_id == repeated.blob_id
    assert first.blob_id != second.blob_id
    assert first.dedup_domain_id.startswith("context-")


def test_platform_scope_reuses_across_tenants_only_when_explicit(
    tmp_path: Path,
) -> None:
    blobs = _runtime(tmp_path, source_scope="platform").blobs("source_asset")

    first = blobs.put_bytes(b"same", context=_context(tenant="tenant-a"))
    second = blobs.put_bytes(b"same", context=_context(tenant="tenant-b"))

    assert first.dedup_domain_id == "platform"
    assert first.blob_id == second.blob_id


def test_none_scope_creates_unique_physical_blobs(tmp_path: Path) -> None:
    blobs = _runtime(tmp_path, source_scope="none").blobs("source_asset")
    context = _context()

    first = blobs.put_bytes(b"same", context=context)
    second = blobs.put_bytes(b"same", context=context)

    assert first.digest == second.digest
    assert first.dedup_domain_id.startswith("instance-")
    assert second.dedup_domain_id.startswith("instance-")
    assert first.blob_id != second.blob_id
    assert first.uri != second.uri
    assert len(_physical_blobs(tmp_path)) == 2


def test_roles_do_not_dedup_across_namespaces(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    context = _context()

    source = runtime.blobs("source_asset").put_bytes(b"same", context=context)
    dataset = runtime.blobs("dataset").put_bytes(b"same", context=context)

    assert source.digest == dataset.digest
    assert source.blob_id != dataset.blob_id
    assert source.storage_key.startswith("source-assets/")
    assert dataset.storage_key.startswith("datasets/")


def test_fallback_blob_ref_records_actual_profile(tmp_path: Path) -> None:
    runtime = _runtime(
        tmp_path,
        source_profile="primary-object",
        fallback_profiles=["local-main"],
        extra_profiles={"primary-object": {"type": "object"}},
    )

    blob = runtime.blobs("source_asset").put_bytes(
        b"same", context=_context()
    )

    assert blob.profile_name == "local-main"
    assert blob.uri.startswith("storage://local-main/source-assets/")


def test_durable_reference_uses_recorded_profile_after_role_change(
    tmp_path: Path,
) -> None:
    old_root = tmp_path / "old"
    new_root = tmp_path / "new"
    profiles = {
        "local-old": {"type": "filesystem", "root": str(old_root)},
        "local-new": {"type": "filesystem", "root": str(new_root)},
    }
    old_config = StorageConfig.from_dict(
        {
            "storage": {
                "profiles": profiles,
                "roles": {
                    "source_asset": {
                        "profile": "local-old",
                        "namespace": "source-assets",
                    }
                },
            }
        }
    )
    old_runtime = StorageRuntime.from_config(old_config)
    reference = old_runtime.blobs("source_asset").put_bytes(
        b"durable", context=_context()
    )
    new_config = StorageConfig.from_dict(
        {
            "storage": {
                "profiles": profiles,
                "roles": {
                    "source_asset": {
                        "profile": "local-new",
                        "namespace": "source-assets",
                    }
                },
            }
        }
    )
    new_runtime = StorageRuntime.from_config(new_config)

    assert new_runtime.for_role("source_asset").profile_name == "local-new"
    with new_runtime.open_blob(reference) as source:
        assert source.read() == b"durable"
    assert new_runtime.resolve_blob_local_path(reference).is_relative_to(old_root)
    assert not new_root.exists()


def test_non_seekable_large_stream_and_temporary_cleanup(tmp_path: Path) -> None:
    class NonSeekable:
        def __init__(self, content: bytes) -> None:
            self._content = content
            self._position = 0

        def read(self, size: int) -> bytes:
            chunk = self._content[self._position : self._position + size]
            self._position += len(chunk)
            return chunk

    spool = tmp_path / "spool"
    spool.mkdir()
    runtime = _runtime(tmp_path / "storage")
    blobs = BlobStore(
        runtime,
        runtime.for_role("source_asset"),
        spool_directory=spool,
    )
    content = b"large-stream-" * (1024 * 256)

    reference = blobs.put_stream(NonSeekable(content), context=_context())

    assert reference.digest == sha256(content).hexdigest()
    assert reference.size_bytes == len(content)
    assert list(spool.iterdir()) == []


def test_stream_temporary_cleanup_on_source_failure(tmp_path: Path) -> None:
    class FailingStream:
        calls = 0

        def read(self, size: int) -> bytes:
            self.calls += 1
            if self.calls == 1:
                return b"partial"
            raise RuntimeError("stream failed")

    spool = tmp_path / "spool"
    spool.mkdir()
    runtime = _runtime(tmp_path / "storage")
    blobs = BlobStore(
        runtime,
        runtime.for_role("source_asset"),
        spool_directory=spool,
    )

    with pytest.raises(RuntimeError, match="stream failed"):
        blobs.put_stream(FailingStream(), context=_context())
    assert list(spool.iterdir()) == []


def test_concurrent_same_content_publication_returns_equivalent_refs(
    tmp_path: Path,
) -> None:
    blobs = _runtime(tmp_path).blobs("source_asset")
    context = _context()

    def publish(_: int):
        return blobs.put_bytes(b"concurrent", context=context)

    with ThreadPoolExecutor(max_workers=2) as workers:
        first, second = workers.map(publish, range(2))

    assert first == second
    assert len(_physical_blobs(tmp_path)) == 1


def test_existing_inconsistent_cas_object_is_not_replaced(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    context = _context()
    content = b"expected"
    digest = sha256(content).hexdigest()
    domain = resolve_dedup_domain(context, "tenant")
    role_key = build_cas_key(domain, digest)
    runtime.for_role("source_asset").put_bytes(role_key, b"wrong")

    with pytest.raises(ObjectConsistencyError, match="Existing CAS object"):
        runtime.blobs("source_asset").put_bytes(content, context=context)
    assert (
        runtime.for_role("source_asset").resolve_local_path(role_key).read_bytes()
        == b"wrong"
    )


def test_blob_store_open_exists_and_local_resolution(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    blobs = runtime.blobs("source_asset")
    reference = blobs.put_bytes(b"content", context=_context())

    assert blobs.exists(reference)
    assert blobs.resolve_local_path(reference).is_file()
    with blobs.open(reference) as source:
        assert source.read() == b"content"
