from pathlib import Path

import pytest

from cognityx_storage import (
    InvalidStorageKeyError,
    LocalStorageBackend,
    StorageClient,
)


@pytest.mark.parametrize(
    "key",
    ["", "/", "../secret", "documents/../secret", "/absolute", r"windows\path"],
)
def test_unsafe_keys_are_rejected(tmp_path: Path, key: str) -> None:
    client = StorageClient(LocalStorageBackend(tmp_path))

    with pytest.raises(InvalidStorageKeyError):
        client.put_bytes(key, b"content")


@pytest.mark.parametrize("user_id", ["../alice", "team/alice", r"team\alice"])
def test_user_id_must_be_one_safe_segment(tmp_path: Path, user_id: str) -> None:
    client = StorageClient(LocalStorageBackend(tmp_path))

    with pytest.raises(InvalidStorageKeyError):
        client.for_user(user_id)


def test_unicode_names_are_supported(tmp_path: Path) -> None:
    client = StorageClient(LocalStorageBackend(tmp_path)).for_user("álîçé")

    stored = client.put_bytes("documents/研究.txt", "hello".encode())

    assert stored.key == "users/álîçé/documents/研究.txt"


def test_existing_symlink_cannot_escape_storage_root(tmp_path: Path) -> None:
    storage_root = tmp_path / "storage"
    storage_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (storage_root / "linked").symlink_to(outside, target_is_directory=True)
    client = StorageClient(LocalStorageBackend(storage_root))

    with pytest.raises(InvalidStorageKeyError):
        client.put_bytes("linked/escaped.txt", b"content")

    assert not (outside / "escaped.txt").exists()

