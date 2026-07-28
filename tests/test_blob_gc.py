from __future__ import annotations

from datetime import timedelta
import os
from pathlib import Path
import time

from cognityx_resource import ResourceContext
from cognityx_storage import StorageConfig, StorageRuntime
from cognityx_storage.runtime import ResolvedRoleStore


def _runtime(root: Path) -> StorageRuntime:
    return StorageRuntime.from_config(StorageConfig.built_in(root=root))


def _old_cas_file(root: Path, storage_key: str | None = None) -> Path:
    if storage_key:
        files = [root / storage_key]
    else:
        files = [
            path
            for path in (root / "source-assets").rglob("*")
            if path.is_file() and "blob-domains" in path.as_posix()
        ]
    assert len(files) >= 1
    old = time.time() - 1000
    os.utime(files[0], (old, old))
    return files[0]


def test_gc_plan_is_dry_run_and_execute_reclaims_only_old_orphans(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    store = runtime.blobs("source_asset")
    context = ResourceContext(tenant_id="tenant-a")
    reference = store.put_bytes(b"live", context=context)
    orphan = store.put_bytes(b"orphan", context=context)
    orphan_path = _old_cas_file(tmp_path, orphan.storage_key)

    plan = runtime.blob_gc().plan(
        referenced_blob_refs=(reference,), older_than=timedelta(days=1)
    )

    assert plan.referenced_blob_count == 1
    assert plan.deletion_candidates == ()
    assert orphan_path.exists()

    plan = runtime.blob_gc().plan(
        referenced_blob_refs=(reference,), older_than=timedelta(seconds=1)
    )
    assert len(plan.deletion_candidates) == 1
    result = runtime.blob_gc().execute(plan, referenced_blob_refs=(reference,))
    assert result.deleted_objects == 1
    assert result.reclaimed_bytes == len(b"orphan")
    assert not orphan_path.exists()
    assert runtime.blob_exists(reference)


def test_gc_skips_metadata_and_malformed_objects(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    source_store = runtime.for_role("source_asset")
    source_store.put_bytes("source-contexts/example/metadata.json", b"metadata")
    source_store.put_bytes("blob-domains/example/not-a-cas-object", b"unknown")

    plan = runtime.blob_gc().plan(older_than=timedelta(seconds=1))

    assert plan.deletion_candidates == ()
    assert len(plan.skipped_objects) >= 1
    assert source_store.exists("source-contexts/example/metadata.json")


def test_gc_revalidates_a_new_reference_before_delete(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    store = runtime.blobs("source_asset")
    context = ResourceContext(tenant_id="tenant-a")
    orphan = store.put_bytes(b"candidate", context=context)
    _old_cas_file(tmp_path, orphan.storage_key)
    plan = runtime.blob_gc().plan(older_than=timedelta(seconds=1))
    assert len(plan.deletion_candidates) == 1

    result = runtime.blob_gc().execute(plan, referenced_blob_refs=(orphan,))

    assert result.deleted_objects == 0
    assert result.skipped_objects == 1
    assert result.skips[0]["reason"] == "now_referenced"
    assert runtime.blob_exists(orphan)


def test_gc_continues_after_ordinary_backend_delete_failure(
    tmp_path: Path, monkeypatch,
) -> None:
    runtime = _runtime(tmp_path)
    store = runtime.blobs("source_asset")
    context = ResourceContext(tenant_id="tenant-a")
    failed_ref = store.put_bytes(b"fail", context=context)
    successful_ref = store.put_bytes(b"success", context=context)
    _old_cas_file(tmp_path, failed_ref.storage_key)
    _old_cas_file(tmp_path, successful_ref.storage_key)
    plan = runtime.blob_gc().plan(older_than=timedelta(seconds=1))
    original_delete = ResolvedRoleStore.delete

    def selective_delete(self, key):
        full_key = f"{self.namespace}/{key}" if self.namespace else key
        if full_key == failed_ref.storage_key:
            raise RuntimeError("simulated backend failure")
        return original_delete(self, key)

    monkeypatch.setattr(ResolvedRoleStore, "delete", selective_delete)

    result = runtime.blob_gc().execute(plan)

    assert result.failed_objects == 1
    assert result.deleted_objects == 1
    assert result.reclaimed_bytes == len(b"success")
    assert result.failures == ({
        "profile": failed_ref.profile_name,
        "storage_key": failed_ref.storage_key,
        "category": "RuntimeError",
        "message": "simulated backend failure",
    },)
    assert runtime.blob_exists(failed_ref)
    assert not runtime.blob_exists(successful_ref)
