# Storage concepts

## Profile

A profile describes one configured storage system: for example `local-main`,
`primary-object`, or `enterprise-hdfs`. It holds a stable Cognityx name, a
storage type, and provider options.

The profile name is durable storage identity. The provider is an
implementation detail.

## Role

A role describes what Cognityx wants to store, such as `source_asset`,
`dataset`, or `catalog`. Roles are configuration names, not a fixed Python
enum. Each role selects a primary profile, optional fallback profiles, and a
logical namespace.

```text
Role: source_asset
    ↓
Profile: primary-object
    ↓ unavailable today
Fallback: local-main
    ↓
LocalStorageBackend
```

Fallback selects a target for operations made through that resolved role. It
does not search other profiles when an object is missing and does not copy or
migrate content.

## Capability

A capability states what an installed provider can actually do. The compact
signature covers streaming, native paths, random writes, locking, hierarchical
namespaces, range reads, object metadata, distribution, and large sequential
I/O.

Configuration may also describe expected capabilities for a future object or
HDFS profile. Expected capabilities do not make a provider available. Only a
registered implementation supplies available capabilities.

Preferred capability mismatches are warnings. They allow a laptop filesystem
to run Cognityx even when distributed or object-metadata semantics would be
better. An operation that fundamentally requires unavailable semantics still
fails explicitly.

## Runtime

`StorageRuntime` connects roles to available profiles and creates a
role-resolved storage client:

```python
storage = StorageRuntime.load()
catalog = storage.for_role("catalog")
datasets = storage.for_role("dataset")
```

Normal code continues to use familiar operations such as `put_file`, `open`,
`exists`, `stat`, and `list`. Diagnostics are available through
`storage.describe()` and properties on the resolved role store.

## Blob

A Blob is immutable stored content. `BlobStore` hashes content, derives its
deduplication domain, publishes it at a content-addressed key, and returns a
durable `BlobRef`.

Blob storage is obtained from the runtime:

```python
blobs = storage.blobs("source_asset")
```

Blob/CAS is a reusable byte-storage capability. It does not define domain
objects such as SourceAsset, DatasetRevision, Checkpoint, or ModelArtifact.
