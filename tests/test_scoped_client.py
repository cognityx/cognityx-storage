import json
from pathlib import Path

from cognityx_storage import LocalStorageBackend, StorageClient


def test_user_clients_are_isolated_by_logical_prefix(tmp_path: Path) -> None:
    root = StorageClient(LocalStorageBackend(tmp_path))
    alice = root.for_user("alice")
    bob = root.for_user("bob")

    alice.put_json("agents/research/memory/state.json", {"answer": 42})

    assert alice.exists("agents/research/memory/state.json")
    assert not bob.exists("agents/research/memory/state.json")
    assert (
        tmp_path / "users/alice/agents/research/memory/state.json"
    ).is_file()
    with alice.open("agents/research/memory/state.json") as source:
        assert json.load(source) == {"answer": 42}


def test_shared_client_uses_shared_namespace(tmp_path: Path) -> None:
    shared = StorageClient(LocalStorageBackend(tmp_path)).for_shared_data()
    stored = shared.put_bytes(
        "rag/document-1/chunks.jsonl",
        b'{"chunk_id":"1"}\n',
        media_type="application/x-ndjson",
    )

    assert stored.key == "shared/rag/document-1/chunks.jsonl"
    assert shared.materialize("rag/document-1/chunks.jsonl").is_file()


def test_client_lists_only_its_scope(tmp_path: Path) -> None:
    root = StorageClient(LocalStorageBackend(tmp_path))
    root.for_user("alice").put_json("documents/one.json", {"id": 1})
    root.for_user("bob").put_json("documents/two.json", {"id": 2})

    assert [item.key for item in root.for_user("alice").list("documents")] == [
        "users/alice/documents/one.json"
    ]

