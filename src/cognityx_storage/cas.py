"""Content hashing, dedup-domain resolution, and CAS identity helpers."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import re
from typing import BinaryIO
from uuid import uuid4

from cognityx_resource import ResourceContext

SHA256 = "sha256"
SUPPORTED_DEDUP_SCOPES = frozenset({"none", "context", "tenant", "platform"})
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_CHUNK_SIZE = 1024 * 1024


def validate_sha256_digest(digest: str) -> str:
    """Return a canonical SHA-256 digest or reject malformed content identity."""
    if not isinstance(digest, str) or not _SHA256_PATTERN.fullmatch(digest):
        raise ValueError("SHA-256 digest must contain exactly 64 lowercase hex characters.")
    return digest


def hash_stream(
    source: BinaryIO,
    *,
    chunk_size: int = _CHUNK_SIZE,
) -> tuple[str, int]:
    """Hash a binary stream incrementally without seeking or retaining its content."""
    if chunk_size <= 0:
        raise ValueError("Hash chunk_size must be positive.")
    digest = sha256()
    size = 0
    while True:
        chunk = source.read(chunk_size)
        if not chunk:
            break
        if not isinstance(chunk, bytes):
            raise TypeError("Blob streams must return bytes.")
        digest.update(chunk)
        size += len(chunk)
    return digest.hexdigest(), size


def copy_and_hash_stream(
    source: BinaryIO,
    destination: BinaryIO,
    *,
    chunk_size: int = _CHUNK_SIZE,
) -> tuple[str, int]:
    """Copy and hash a potentially non-seekable stream with bounded memory."""
    if chunk_size <= 0:
        raise ValueError("Hash chunk_size must be positive.")
    digest = sha256()
    size = 0
    while True:
        chunk = source.read(chunk_size)
        if not chunk:
            break
        if not isinstance(chunk, bytes):
            raise TypeError("Blob streams must return bytes.")
        destination.write(chunk)
        digest.update(chunk)
        size += len(chunk)
    return digest.hexdigest(), size


def hash_file(
    path: str | Path,
    *,
    chunk_size: int = _CHUNK_SIZE,
) -> tuple[str, int]:
    """Hash one local file incrementally."""
    selected = Path(path)
    if not selected.is_file():
        raise FileNotFoundError(f"Blob source file does not exist: {selected}")
    with selected.open("rb") as source:
        return hash_stream(source, chunk_size=chunk_size)


def resolve_dedup_domain(
    context: ResourceContext,
    scope: str = "tenant",
    *,
    instance_id: str | None = None,
) -> str:
    """Return a non-identifying physical reuse domain for one Blob write."""
    if scope not in SUPPORTED_DEDUP_SCOPES:
        raise ValueError(
            "dedup_scope must be one of: "
            + ", ".join(sorted(SUPPORTED_DEDUP_SCOPES))
        )
    if scope == "platform":
        return "platform"
    if scope == "none":
        token = instance_id or uuid4().hex
        return f"instance-{_domain_token(token)}"
    if scope == "context":
        return f"context-{_domain_token(context.context_id)}"
    if context.context_type == "system":
        return f"system-{_domain_token(context.context_id)}"
    if context.tenant_id:
        return f"tenant-{_domain_token(context.tenant_id)}"
    return f"principal-{_domain_token(context.principal_id or context.context_id)}"


def build_cas_key(
    dedup_domain_id: str,
    digest: str,
    *,
    algorithm: str = SHA256,
) -> str:
    """Build the role-relative immutable content address."""
    if algorithm != SHA256:
        raise ValueError(f"Unsupported Blob digest algorithm: {algorithm}")
    if (
        not isinstance(dedup_domain_id, str)
        or not dedup_domain_id
        or "/" in dedup_domain_id
        or "\\" in dedup_domain_id
    ):
        raise ValueError("Dedup domain ID must be one non-empty storage segment.")
    canonical = validate_sha256_digest(digest)
    return (
        f"blob-domains/{dedup_domain_id}/{algorithm}/"
        f"{canonical[:2]}/{canonical[2:4]}/{canonical}"
    )


def derive_blob_id(profile_name: str, storage_key: str) -> str:
    """Derive stable Blob identity from Cognityx profile and logical object identity."""
    if not profile_name or not storage_key:
        raise ValueError("Blob profile name and storage key cannot be empty.")
    token = sha256(f"{profile_name}:{storage_key}".encode()).hexdigest()[:24]
    return f"blob-{token}"


def _domain_token(value: str) -> str:
    return sha256(value.encode()).hexdigest()[:20]
