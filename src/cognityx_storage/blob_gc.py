"""Dry-run-first, reference-safe Blob garbage collection."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
import re
from typing import Any
from uuid import uuid4

from cognityx_storage.blob import BlobRef
from cognityx_storage.exceptions import ObjectNotFoundError, StorageError
from cognityx_storage.runtime import StorageRuntime, ResolvedRoleStore

_CAS_PATTERN = re.compile(
    r"^blob-domains/[^/]+/sha256/[0-9a-f]{2}/[0-9a-f]{2}/[0-9a-f]{64}$"
)


@dataclass(frozen=True, slots=True)
class BlobGcCandidate:
    profile_name: str
    storage_key: str
    uri: str
    blob_id: str
    size_bytes: int
    last_modified: float
    digest: str
    reason: str = "unreferenced_and_older_than_grace_period"


@dataclass(frozen=True, slots=True)
class BlobGcPlan:
    plan_id: str
    created_at: str
    role_name: str
    grace_period_seconds: float
    profiles_scanned: tuple[str, ...]
    objects_scanned: int
    referenced_blob_count: int
    unreferenced_blob_count: int
    protected_by_grace_period: int
    deletion_candidates: tuple[BlobGcCandidate, ...]
    reclaimable_bytes: int
    skipped_objects: tuple[dict[str, str], ...] = ()
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        result = {
            "plan_id": self.plan_id,
            "created_at": self.created_at,
            "role_name": self.role_name,
            "grace_period_seconds": self.grace_period_seconds,
            "profiles_scanned": list(self.profiles_scanned),
            "objects_scanned": self.objects_scanned,
            "referenced_blob_count": self.referenced_blob_count,
            "unreferenced_blob_count": self.unreferenced_blob_count,
            "protected_by_grace_period": self.protected_by_grace_period,
            "deletion_candidates": [asdict(candidate) for candidate in self.deletion_candidates],
            "reclaimable_bytes": self.reclaimable_bytes,
            "skipped_objects": list(self.skipped_objects),
            "warnings": list(self.warnings),
        }
        return result


@dataclass(frozen=True, slots=True)
class BlobGcResult:
    plan_id: str
    deleted_objects: int
    already_absent: int
    skipped_objects: int
    failed_objects: int
    reclaimed_bytes: int
    failures: tuple[dict[str, str], ...] = ()
    skips: tuple[dict[str, str], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "deleted_objects": self.deleted_objects,
            "already_absent": self.already_absent,
            "skipped_objects": self.skipped_objects,
            "failed_objects": self.failed_objects,
            "reclaimed_bytes": self.reclaimed_bytes,
            "failures": list(self.failures),
            "skips": list(self.skips),
        }


class BlobGarbageCollector:
    """Storage-owned physical Blob planner and executor."""

    def __init__(self, runtime: StorageRuntime, *, role_name: str = "source_asset"):
        self._runtime = runtime
        self._role_name = role_name

    def plan(
        self,
        *,
        referenced_blob_refs: tuple[BlobRef, ...] | list[BlobRef] = (),
        older_than: timedelta = timedelta(days=7),
        profile_hint_blob_refs: tuple[BlobRef, ...] | list[BlobRef] = (),
    ) -> BlobGcPlan:
        if older_than <= timedelta(0):
            raise ValueError("older_than must be greater than zero.")
        references = {
            (ref.profile_name, ref.storage_key) for ref in referenced_blob_refs
        }
        stores, warnings = self._stores(referenced_blob_refs, profile_hint_blob_refs)
        now = datetime.now(UTC).timestamp()
        grace_seconds = older_than.total_seconds()
        candidates: list[BlobGcCandidate] = []
        skipped: list[dict[str, str]] = []
        scanned = protected = unreferenced = 0
        for profile_name, store in stores:
            try:
                objects = self._walk(store, "blob-domains")
            except StorageError as exc:
                skipped.append({"profile": profile_name, "error": str(exc)})
                continue
            for item, relative_key in objects:
                scanned += 1
                if item.is_directory or not _CAS_PATTERN.fullmatch(relative_key):
                    skipped.append({"profile": profile_name, "storage_key": item.key, "reason": "malformed_or_unknown_object"})
                    continue
                identity = (profile_name, item.key)
                if identity in references:
                    continue
                unreferenced += 1
                if item.last_modified is None:
                    skipped.append({"profile": profile_name, "storage_key": item.key, "reason": "age_unknown"})
                    continue
                if now - item.last_modified < grace_seconds:
                    protected += 1
                    continue
                digest = relative_key.rsplit("/", 1)[-1]
                candidates.append(
                    BlobGcCandidate(
                        profile_name,
                        item.key,
                        item.uri,
                        _blob_id(profile_name, item.key),
                        item.size_bytes,
                        item.last_modified,
                        digest,
                    )
                )
        return BlobGcPlan(
            plan_id=f"gc-{uuid4().hex}",
            created_at=datetime.now(UTC).isoformat(),
            role_name=self._role_name,
            grace_period_seconds=grace_seconds,
            profiles_scanned=tuple(name for name, _ in stores),
            objects_scanned=scanned,
            referenced_blob_count=len(references),
            unreferenced_blob_count=unreferenced,
            protected_by_grace_period=protected,
            deletion_candidates=tuple(candidates),
            reclaimable_bytes=sum(candidate.size_bytes for candidate in candidates),
            skipped_objects=tuple(skipped),
            warnings=tuple(warnings),
        )

    def execute(
        self,
        plan: BlobGcPlan,
        *,
        referenced_blob_refs: tuple[BlobRef, ...] | list[BlobRef] = (),
    ) -> BlobGcResult:
        references = {
            (ref.profile_name, ref.storage_key) for ref in referenced_blob_refs
        }
        stores = dict(self._stores(referenced_blob_refs, include_plan=plan)[0])
        deleted = absent = skipped = failed = reclaimed = 0
        failures: list[dict[str, str]] = []
        skips: list[dict[str, str]] = []
        now = datetime.now(UTC).timestamp()
        for candidate in plan.deletion_candidates:
            identity = (candidate.profile_name, candidate.storage_key)
            if identity in references:
                skipped += 1
                skips.append({"profile": candidate.profile_name, "storage_key": candidate.storage_key, "reason": "now_referenced"})
                continue
            store = stores.get(candidate.profile_name)
            if store is None:
                skipped += 1
                skips.append({"profile": candidate.profile_name, "storage_key": candidate.storage_key, "reason": "profile_unavailable"})
                continue
            relative = candidate.storage_key.removeprefix(store.namespace + "/")
            try:
                current = store.stat(relative)
                if current.size_bytes != candidate.size_bytes or current.last_modified != candidate.last_modified:
                    skipped += 1
                    skips.append({"profile": candidate.profile_name, "storage_key": candidate.storage_key, "reason": "object_changed"})
                    continue
                if current.last_modified is None or now - current.last_modified < plan.grace_period_seconds:
                    skipped += 1
                    skips.append({"profile": candidate.profile_name, "storage_key": candidate.storage_key, "reason": "too_recent"})
                    continue
                if not _CAS_PATTERN.fullmatch(relative):
                    skipped += 1
                    skips.append({"profile": candidate.profile_name, "storage_key": candidate.storage_key, "reason": "invalid_cas_key"})
                    continue
                store.delete(relative)
                deleted += 1
                reclaimed += candidate.size_bytes
            except StorageError as exc:
                if isinstance(exc, ObjectNotFoundError):
                    absent += 1
                    skips.append({"profile": candidate.profile_name, "storage_key": candidate.storage_key, "reason": "already_absent"})
                else:
                    failed += 1
                    failures.append({
                        "profile": candidate.profile_name,
                        "storage_key": candidate.storage_key,
                        "error": str(exc),
                    })
            except Exception as exc:
                failed += 1
                failures.append({"profile": candidate.profile_name, "storage_key": candidate.storage_key,
                                 "category": type(exc).__name__, "message": str(exc)})
        return BlobGcResult(plan.plan_id, deleted, absent, skipped, failed, reclaimed,
                            tuple(failures), tuple(skips))

    def _stores(
        self,
        refs: tuple[BlobRef, ...] | list[BlobRef],
        hints: tuple[BlobRef, ...] | list[BlobRef] = (),
        *,
        include_plan: BlobGcPlan | None = None,
    ) -> tuple[list[tuple[str, ResolvedRoleStore]], list[str]]:
        role = self._runtime.config.roles.get(self._role_name)
        names: set[str] = set()
        if role is not None:
            names.add(role.profile or "")
            names.update(role.fallback_profiles)
        names.discard("")
        try:
            names.add(self._runtime.for_role(self._role_name).profile_name)
        except StorageError:
            pass
        names.update(ref.profile_name for ref in refs if ref.role_name == self._role_name)
        names.update(ref.profile_name for ref in hints if ref.role_name == self._role_name)
        if include_plan:
            names.update(candidate.profile_name for candidate in include_plan.deletion_candidates)
        stores: list[tuple[str, ResolvedRoleStore]] = []
        warnings: list[str] = []
        for name in names:
            try:
                stores.append((name, self._runtime.for_profile(name, role_name=self._role_name)))
            except StorageError:
                warnings.append(f"profile {name}: unavailable for role {self._role_name}")
                continue
        return stores, warnings

    @staticmethod
    def _walk(store: ResolvedRoleStore, prefix: str):
        for item in store.list(prefix):
            relative = item.key.removeprefix(store.namespace + "/")
            if item.is_directory:
                yield from BlobGarbageCollector._walk(store, relative)
            else:
                yield item, relative


def _blob_id(profile_name: str, storage_key: str) -> str:
    from cognityx_storage.cas import derive_blob_id

    return derive_blob_id(profile_name, storage_key)
