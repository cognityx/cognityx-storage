from __future__ import annotations

from dataclasses import replace
from hashlib import sha256

import pytest

from cognityx_storage import BlobRef, build_cas_key, derive_blob_id


def _blob_ref() -> BlobRef:
    digest = sha256(b"blob").hexdigest()
    storage_key = (
        f"source-assets/blob-domains/tenant-abc/sha256/"
        f"{digest[:2]}/{digest[2:4]}/{digest}"
    )
    return BlobRef(
        blob_id=derive_blob_id("local-main", storage_key),
        role_name="source_asset",
        profile_name="local-main",
        uri=f"storage://local-main/{storage_key}",
        storage_key=storage_key,
        algorithm="sha256",
        digest=digest,
        dedup_domain_id="tenant-abc",
        size_bytes=4,
        media_type="application/octet-stream",
    )


def test_blob_ref_serialization_round_trip() -> None:
    reference = _blob_ref()

    assert BlobRef.from_dict(reference.to_dict()) == reference
    assert not any(
        name in reference.to_dict()
        for name in ("path", "local_path", "source_path", "filename")
    )


@pytest.mark.parametrize(
    "change, message",
    [
        ({"digest": "not-a-digest"}, "64 lowercase hex"),
        ({"algorithm": "md5"}, "Unsupported Blob digest"),
        ({"profile_name": "bad/profile"}, "profile_name"),
        ({"role_name": "bad role"}, "role_name"),
        ({"dedup_domain_id": "tenant/acme"}, "dedup_domain_id"),
        ({"size_bytes": -1}, "non-negative"),
        ({"size_bytes": True}, "non-negative"),
    ],
)
def test_blob_ref_rejects_invalid_invariants(change, message: str) -> None:
    reference = _blob_ref()

    with pytest.raises(ValueError, match=message):
        replace(reference, **change)


def test_blob_ref_uri_and_identity_must_match_physical_object() -> None:
    reference = _blob_ref()

    with pytest.raises(ValueError, match="uri must match"):
        replace(reference, uri="storage://other/source-assets/blob")
    with pytest.raises(ValueError, match="blob_id must match"):
        replace(reference, blob_id="blob-other")


def test_blob_identity_is_stable_for_same_profile_and_key() -> None:
    reference = _blob_ref()

    assert reference.blob_id == derive_blob_id(
        reference.profile_name, reference.storage_key
    )
    assert reference.blob_id != derive_blob_id(
        "other-profile", reference.storage_key
    )


def test_cas_key_uses_digest_fanout() -> None:
    digest = sha256(b"blob").hexdigest()

    assert build_cas_key("tenant-abc", digest) == (
        f"blob-domains/tenant-abc/sha256/"
        f"{digest[:2]}/{digest[2:4]}/{digest}"
    )
