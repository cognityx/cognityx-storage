# Cognityx Storage

`cognityx-storage` gives Cognityx services one small storage API without making
them depend on filesystem paths or cloud SDKs. The first backend is a pass-through
to a local filesystem. Future backends can map the same logical keys to object or
cloud storage.

The default local data root is:

```text
/mnt/d/AI/cognitive/cognityx-storage
```

Constructing a client does not create the directory. The backend creates only
the parent directories needed by the first write.

## Basic use

```python
from cognityx_storage import LocalStorageBackend, StorageClient

storage = StorageClient(LocalStorageBackend())
user_storage = storage.for_user("alice")

document = user_storage.put_file(
    "documents/report-001/source.pdf",
    "/tmp/upload.pdf",
)

user_storage.put_json(
    "agents/research-agent/checkpoints/checkpoint-001.json",
    {"last_document": document.key},
)

with user_storage.open("agents/research-agent/checkpoints/checkpoint-001.json") as source:
    checkpoint = source.read()
```

Shared platform data uses a separate scope:

```python
shared = storage.for_shared_data()
shared.put_directory("models/qwen-adapter/version-001", "/tmp/qwen-adapter")
```

## RAG boundary

Original documents, extracted text, chunks, and provenance belong in this
storage layer. A vector database stores embeddings and references the stable
logical keys of those chunks.

JSON is appropriate for metadata and checkpoints. JSONL is a practical initial
format for chunks and modest datasets. Dataset or ingestion components may
publish Parquet for larger structured data; storage treats all of these formats
as opaque content.

## Scope and security

`for_user()` and `for_shared_data()` prevent callers from accidentally building
paths outside their logical namespace. Keys reject absolute paths, `..`, control
characters, and platform-specific separators.

To remove an object, use the scoped client. Directory deletion requires an
explicit recursive opt-in:

```python
shared.delete("ingest/documents/doc-123", recursive=True)
```

This first local backend is not a production authorization system. A future
authenticated client or storage service can enforce tenant identities, roles,
audit logging, and encryption while preserving the application-facing storage
operations.

## Development

```text
uv sync --extra dev
uv run pytest
uv build
```
