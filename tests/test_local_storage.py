from io import BytesIO
from pathlib import Path

import pytest

from cognityx_storage import (
    LocalStorageBackend,
    ObjectAlreadyExistsError,
    ObjectNotFoundError,
    UnsupportedOperationError,
)


def test_stream_round_trip_and_stat(tmp_path: Path) -> None:
    backend = LocalStorageBackend(tmp_path)

    stored = backend.put_stream(
        "raw/example/source.txt",
        BytesIO(b"hello"),
        media_type="text/plain",
    )

    assert stored.key == "raw/example/source.txt"
    assert stored.size_bytes == 5
    assert stored.media_type == "text/plain"
    assert stored.uri.startswith("file://")
    with backend.open_reader(stored.key) as source:
        assert source.read() == b"hello"
    assert backend.stat(stored.key).size_bytes == 5


def test_duplicate_publication_is_rejected(tmp_path: Path) -> None:
    backend = LocalStorageBackend(tmp_path)
    backend.put_stream("reports/result.json", BytesIO(b"{}"))

    with pytest.raises(ObjectAlreadyExistsError):
        backend.put_stream("reports/result.json", BytesIO(b"replacement"))

    assert (tmp_path / "reports/result.json").read_bytes() == b"{}"


def test_file_and_directory_publication(tmp_path: Path) -> None:
    source_file = tmp_path / "source.jsonl"
    source_file.write_text('{"text":"one"}\n', encoding="utf-8")
    source_directory = tmp_path / "checkpoint"
    source_directory.mkdir()
    (source_directory / "adapter.json").write_text("{}", encoding="utf-8")

    backend = LocalStorageBackend(tmp_path / "storage")
    stored_file = backend.put_file("datasets/example/data.jsonl", source_file)
    stored_directory = backend.put_directory(
        "models/example/version-1", source_directory
    )

    assert stored_file.media_type == "application/x-ndjson"
    assert not stored_file.is_directory
    assert stored_directory.is_directory
    assert stored_directory.size_bytes == 2
    assert (
        backend.materialize("models/example/version-1") / "adapter.json"
    ).read_text(encoding="utf-8") == "{}"
    with pytest.raises(UnsupportedOperationError):
        backend.open_reader("models/example/version-1")


def test_listing_is_sorted_and_non_recursive(tmp_path: Path) -> None:
    backend = LocalStorageBackend(tmp_path)
    backend.put_stream("runs/run-2/report.json", BytesIO(b"{}"))
    backend.put_stream("runs/run-1/report.json", BytesIO(b"{}"))

    assert [item.key for item in backend.list("runs")] == [
        "runs/run-1",
        "runs/run-2",
    ]


def test_missing_source_and_object_are_reported(tmp_path: Path) -> None:
    backend = LocalStorageBackend(tmp_path)

    with pytest.raises(ObjectNotFoundError):
        backend.put_file("raw/missing", tmp_path / "missing")
    with pytest.raises(ObjectNotFoundError):
        backend.stat("raw/missing")
