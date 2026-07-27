"""Immutable Blob references and stateless CAS publication."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from io import BytesIO
import mimetypes
from pathlib import Path
import re
import tempfile
from typing import TYPE_CHECKING, Any, BinaryIO, Mapping

from cognityx_resource import ResourceContext

from cognityx_storage.cas import (
    SHA256,
    build_cas_key,
    copy_and_hash_stream,
    derive_blob_id,
    hash_stream,
    resolve_dedup_domain,
    validate_sha256_digest,
)
from cognityx_storage.exceptions import (
    ObjectAlreadyExistsError,
    ObjectConsistencyError,
)
from cognityx_storage.local import validate_storage_key

if TYPE_CHECKING:
    from cognityx_storage.runtime import ResolvedRoleStore, StorageRuntime

_IDENTITY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_DOMAIN_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")


@dataclass(frozen=True, slots=True)
class BlobRef:
    """Durable provider-neutral reference to immutable stored bytes."""

    blob_id: str
    role_name: str
    profile_name: str
    uri: str
    storage_key: str
    algorithm: str
    digest: str
    dedup_domain_id: str
    size_bytes: int
    media_type: str

    def __post_init__(self) -> None:
        for field_name in (
            "blob_id",
            "role_name",
            "profile_name",
            "uri",
            "storage_key",
            "algorithm",
            "digest",
            "dedup_domain_id",
            "media_type",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"BlobRef {field_name} must be a non-empty string.")
        if self.algorithm != SHA256:
            raise ValueError(f"Unsupported Blob digest algorithm: {self.algorithm}")
        validate_sha256_digest(self.digest)
        if not isinstance(self.size_bytes, int) or isinstance(self.size_bytes, bool):
            raise ValueError("BlobRef size_bytes must be a non-negative integer.")
        if self.size_bytes < 0:
            raise ValueError("BlobRef size_bytes must be a non-negative integer.")
        if not _IDENTITY_PATTERN.fullmatch(self.profile_name):
            raise ValueError("BlobRef profile_name is not a valid storage identity.")
        if not _IDENTITY_PATTERN.fullmatch(self.role_name):
            raise ValueError("BlobRef role_name is not a valid storage role.")
        validate_storage_key(self.storage_key)
        if not _DOMAIN_PATTERN.fullmatch(self.dedup_domain_id):
            raise ValueError("BlobRef dedup_domain_id is not valid.")
        expected_uri = f"storage://{self.profile_name}/{self.storage_key}"
        if self.uri != expected_uri:
            raise ValueError(
                "BlobRef uri must match its profile_name and storage_key."
            )
        expected_blob_id = derive_blob_id(self.profile_name, self.storage_key)
        if self.blob_id != expected_blob_id:
            raise ValueError(
                "BlobRef blob_id must match its profile and storage key identity."
            )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "BlobRef":
        return cls(
            blob_id=value["blob_id"],
            role_name=value["role_name"],
            profile_name=value["profile_name"],
            uri=value["uri"],
            storage_key=value["storage_key"],
            algorithm=value["algorithm"],
            digest=value["digest"],
            dedup_domain_id=value["dedup_domain_id"],
            size_bytes=value["size_bytes"],
            media_type=value["media_type"],
        )


@dataclass(frozen=True, slots=True)
class ContentDigest:
    """Provider-neutral identity of one captured, unpublished byte snapshot."""

    algorithm: str
    digest: str
    size_bytes: int
    media_type: str

    def __post_init__(self) -> None:
        if self.algorithm != SHA256:
            raise ValueError(
                f"Unsupported content digest algorithm: {self.algorithm}"
            )
        validate_sha256_digest(self.digest)
        if not isinstance(self.size_bytes, int) or isinstance(
            self.size_bytes, bool
        ):
            raise ValueError(
                "ContentDigest size_bytes must be a non-negative integer."
            )
        if self.size_bytes < 0:
            raise ValueError(
                "ContentDigest size_bytes must be a non-negative integer."
            )
        if not isinstance(self.media_type, str) or not self.media_type:
            raise ValueError(
                "ContentDigest media_type must be a non-empty string."
            )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PreparedBlob:
    """One unpublished staged snapshot owned by a :class:`BlobStore`."""

    def __init__(
        self,
        blob_store: "BlobStore",
        temporary: tempfile.TemporaryDirectory[str],
        path: Path,
        content: ContentDigest,
    ) -> None:
        self._blob_store = blob_store
        self._temporary = temporary
        self._path = path
        self._content = content
        self._active = True
        self._published = False

    @property
    def content(self) -> ContentDigest:
        return self._content

    @property
    def algorithm(self) -> str:
        return self._content.algorithm

    @property
    def digest(self) -> str:
        return self._content.digest

    @property
    def size_bytes(self) -> int:
        return self._content.size_bytes

    @property
    def media_type(self) -> str:
        return self._content.media_type

    def publish(self, *, context: ResourceContext) -> BlobRef:
        """Publish the captured bytes once through the owning BlobStore."""
        if not self._active:
            raise RuntimeError("PreparedBlob is closed and cannot be published.")
        if self._published:
            raise RuntimeError("PreparedBlob has already been published.")
        reference = self._blob_store._publish(
            self._path,
            digest=self.digest,
            size_bytes=self.size_bytes,
            media_type=self.media_type,
            context=context,
        )
        self._published = True
        return reference

    def close(self) -> None:
        """Discard the temporary snapshot if it is still active."""
        if self._active:
            self._temporary.cleanup()
            self._active = False

    def __enter__(self) -> "PreparedBlob":
        if not self._active:
            raise RuntimeError("PreparedBlob is already closed.")
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()


class BlobStore:
    """Publish immutable content through one resolved storage role."""

    def __init__(
        self,
        runtime: "StorageRuntime",
        role_store: "ResolvedRoleStore",
        *,
        dedup_scope: str = "tenant",
        spool_directory: str | Path | None = None,
    ) -> None:
        self._runtime = runtime
        self._store = role_store
        self.dedup_scope = dedup_scope
        self._spool_directory = (
            Path(spool_directory) if spool_directory is not None else None
        )

    @property
    def role_name(self) -> str:
        return self._store.role_name

    @property
    def profile_name(self) -> str:
        return self._store.profile_name

    def put_file(
        self,
        source: str | Path,
        *,
        context: ResourceContext,
        media_type: str | None = None,
    ) -> BlobRef:
        with self.prepare_file(source, media_type=media_type) as prepared:
            return prepared.publish(context=context)

    def prepare_file(
        self,
        source: str | Path,
        *,
        media_type: str | None = None,
    ) -> PreparedBlob:
        """Capture one file snapshot without publishing a durable Blob."""
        path = Path(source)
        if not path.is_file():
            raise FileNotFoundError(f"Blob source file does not exist: {path}")
        selected_media_type = (
            media_type
            or mimetypes.guess_type(path.name)[0]
            or "application/octet-stream"
        )
        with path.open("rb") as source_stream:
            return self._prepare_stream(
                source_stream,
                media_type=selected_media_type,
            )

    def put_stream(
        self,
        source: BinaryIO,
        *,
        context: ResourceContext,
        media_type: str = "application/octet-stream",
    ) -> BlobRef:
        with self.prepare_stream(source, media_type=media_type) as prepared:
            return prepared.publish(context=context)

    def prepare_stream(
        self,
        source: BinaryIO,
        *,
        media_type: str = "application/octet-stream",
    ) -> PreparedBlob:
        """Capture one stream snapshot without publishing a durable Blob."""
        return self._prepare_stream(source, media_type=media_type)

    def _prepare_stream(
        self,
        source: BinaryIO,
        *,
        media_type: str,
    ) -> PreparedBlob:
        temporary = tempfile.TemporaryDirectory(
            prefix="cognityx-blob-",
            dir=self._spool_directory,
        )
        try:
            path = Path(temporary.name) / "content"
            with path.open("wb") as destination:
                digest, size = copy_and_hash_stream(source, destination)
                destination.flush()
            content = ContentDigest(
                algorithm=SHA256,
                digest=digest,
                size_bytes=size,
                media_type=media_type,
            )
            return PreparedBlob(
                self,
                temporary,
                path,
                content,
            )
        except BaseException:
            temporary.cleanup()
            raise

    def put_bytes(
        self,
        content: bytes,
        *,
        context: ResourceContext,
        media_type: str = "application/octet-stream",
    ) -> BlobRef:
        return self.put_stream(
            BytesIO(content),
            context=context,
            media_type=media_type,
        )

    def open(self, blob_ref: BlobRef) -> BinaryIO:
        return self._runtime.open_blob(blob_ref)

    def exists(self, blob_ref: BlobRef) -> bool:
        return self._runtime.blob_exists(blob_ref)

    def resolve_local_path(self, blob_ref: BlobRef) -> Path | None:
        return self._runtime.resolve_blob_local_path(blob_ref)

    def _publish(
        self,
        path: Path,
        *,
        digest: str,
        size_bytes: int,
        media_type: str,
        context: ResourceContext,
    ) -> BlobRef:
        domain = resolve_dedup_domain(context, self.dedup_scope)
        role_key = build_cas_key(domain, digest)
        try:
            stored = self._store.put_file(
                role_key,
                path,
                media_type=media_type,
            )
        except ObjectAlreadyExistsError:
            self._verify_existing(role_key, digest, size_bytes)
            storage_key = f"{self._store.namespace}/{role_key}"
            uri = self._store.uri(role_key)
        else:
            storage_key = stored.key
            uri = stored.uri
        return BlobRef(
            blob_id=derive_blob_id(self.profile_name, storage_key),
            role_name=self.role_name,
            profile_name=self.profile_name,
            uri=uri,
            storage_key=storage_key,
            algorithm=SHA256,
            digest=digest,
            dedup_domain_id=domain,
            size_bytes=size_bytes,
            media_type=media_type,
        )

    def _verify_existing(
        self,
        role_key: str,
        expected_digest: str,
        expected_size: int,
    ) -> None:
        with self._store.open(role_key) as existing:
            actual_digest, actual_size = hash_stream(existing)
        if (
            actual_digest != expected_digest
            or actual_size != expected_size
        ):
            raise ObjectConsistencyError(
                f"Existing CAS object conflicts with expected immutable content: "
                f"{role_key}"
            )
