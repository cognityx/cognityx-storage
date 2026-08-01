# Cognityx Storage

[![CI](https://github.com/cognityx/cognityx-storage/actions/workflows/ci.yml/badge.svg)](https://github.com/cognityx/cognityx-storage/actions/workflows/ci.yml)

`cognityx-storage` gives Cognityx services one small storage API without making
them select filesystem paths or provider SDKs.

```text
Cognityx applications
        ↓ logical role such as source_asset or artifact
  Cognityx Storage Runtime
        ↓ configured profile
filesystem today, other providers when available
```

For original source files, Storage also calculates a SHA-256 fingerprint,
stores bytes by that fingerprint, and safely reuses identical content. Storing
content by its fingerprint is technically called content-addressed storage
(CAS).

## Quick start

```python
from cognityx_storage import StorageRuntime

storage = StorageRuntime.load()
assets = storage.for_role("source_asset")

stored = assets.put_file(
    "incoming/report.pdf",
    "/tmp/report.pdf",
)

print(stored.uri)
# storage://local-main/source-assets/incoming/report.pdf
```

With no configuration, the runtime uses the existing default local root and
defines roles for catalogs, source assets, artifacts, datasets, models, caches,
and temporary content. Directories are created only by the first write.

To select a project configuration explicitly:

```python
storage = StorageRuntime.load(
    config_file=".cognityx/storage.toml",
)
datasets = storage.for_role("dataset")
```

Inspect routing without exposing provider secrets:

```python
report = storage.describe()
```

Immutable content-addressed Blobs use the same runtime:

```python
from cognityx_resource import ResourceContext

context = ResourceContext(tenant_id="acme", principal_id="alice")
blob = storage.blobs("source_asset").put_file(
    "/tmp/report.pdf",
    context=context,
)

with storage.open_blob(blob) as source:
    content = source.read()
```

## Profiles and roles

A profile describes where and how storage exists. A role describes what a
Cognityx service wants to store. The runtime resolves the role's preferred
profile or the first available fallback.

Only the filesystem provider performs I/O today. Object and HDFS profiles may
be configured for forward-compatible deployment plans, but they are reported
as unavailable and require an available fallback.

See [Storage concepts](docs/concepts.md), [Blob/CAS usage](docs/blobs.md),
[configuration](docs/configuration.md), and
[architecture](docs/architecture.md).

## Existing low-level API

`StorageClient`, `LocalStorageBackend`, `for_user()`, and `for_shared_data()`
remain supported. Their existing key and URI behavior is unchanged so already
published Ingest metadata remains valid. New services should normally begin
with `StorageRuntime`.

This package does not implement application authorization, cross-provider
replication, or automatic data migration. It does implement SHA-256 hashing,
content-addressed Blob storage, configurable deduplication boundaries, physical
Blob reuse, and reference-aware Blob garbage collection.

## Deletion And Cleanup

Applications first remove their logical references. Storage then plans which
Blobs have no remaining live reference. Planning is non-destructive; execution
rechecks references and requires explicit confirmation from the caller before
physical deletion.

## Future Roadmap

A future Storage service will run this same safe cleanup process continuously
in the background according to a retention policy. It will reuse the existing
garbage-collection checks rather than create a second deletion mechanism. The
current package remains an embedded runtime, so automatic scheduling is
intentionally deferred until the long-running Storage service boundary is
defined.

## Contributing

```bash
uv sync --extra dev
uv run pytest
uv run mkdocs build --strict
uv build
```

GitHub CI runs these same convention-based commands and automatically collects
every test below `tests/`.
