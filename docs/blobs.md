# Blobs and content-addressed storage

## Quick start

```python
from cognityx_resource import ResourceContext
from cognityx_storage import StorageRuntime

context = ResourceContext(
    tenant_id="acme",
    principal_id="alice",
)

storage = StorageRuntime.load()
blobs = storage.blobs("source_asset")

blob = blobs.put_file(
    "/tmp/report.pdf",
    context=context,
)

print(blob.blob_id)
print(blob.uri)
```

Read the durable reference later:

```python
with storage.open_blob(blob) as stream:
    content = stream.read()
```

The read uses the profile recorded in `BlobRef`. It does not re-run current
role fallback selection.

## Concepts

**Blob** means immutable bytes stored by Cognityx.

**Content-addressed storage (CAS)** places those bytes at a logical address
derived from their SHA-256 digest.

**Dedup scope** is the boundary inside which identical bytes may share one
physical object.

**BlobRef** is the durable, serializable description of the stored bytes. It
contains Blob identity, role and profile identity, provider-neutral URI,
logical storage key, digest, dedup domain, size, and descriptive media type.
It never contains the caller's filesystem path.

`ResourceRef` and `BlobRef` serve different purposes. `ResourceRef` points to a
Cognityx domain resource. `BlobRef` points to immutable bytes.

## Other write forms

```python
blob = blobs.put_bytes(
    b"content",
    context=context,
    media_type="text/plain",
)

blob = blobs.put_stream(
    incoming_stream,
    context=context,
    media_type="application/octet-stream",
)
```

Files and streams are copied and hashed incrementally into one temporary
snapshot. The exact staged bytes used to calculate the CAS address are then
published, so later changes to a caller's file cannot alter the Blob. Streams
do not need to support seeking. Staged content is cleaned after success or
failure.

## Inspect before publication

A domain service may need content identity before deciding whether to accept a
new logical resource. Storage can capture an unpublished snapshot:

```python
with blobs.prepare_file("/tmp/report.pdf") as prepared:
    print(prepared.digest, prepared.size_bytes, prepared.media_type)

    if should_accept(prepared.content):
        blob = prepared.publish(context=context)
```

`prepared.content` is an immutable `ContentDigest`; it contains the algorithm,
digest, size and media type, but no CAS key, dedup domain or physical location.
`prepare_stream()` provides the same behavior for a binary stream.

The snapshot is published at most once. Leaving the context without publishing
discards it. Successful publication, caller exceptions and backend failures all
clean the temporary content, and a closed `PreparedBlob` cannot be published.
The ordinary `put_file()`, `put_stream()` and `put_bytes()` methods use the
same preparation pipeline internally.

## Deduplication behavior

The role's `dedup_scope` controls physical reuse:

| Scope | Physical reuse boundary |
| --- | --- |
| `tenant` | Same tenant; tenantless users remain separated by principal |
| `context` | Same stable `ResourceContext` only |
| `platform` | Entire deployment, only when explicitly configured |
| `none` | No physical reuse; every write has a unique instance domain |

System Contexts use a separate stable system domain. Physical keys contain
cryptographic tokens rather than raw tenant, principal, or service names.

BlobStore returns the same `BlobRef` shape whether content was newly written or
physically reused. It does not disclose another caller's prior storage activity.
Filename and media type are not part of content identity.

## Durable inspection

```python
assert storage.blob_exists(blob)
local = storage.resolve_blob_local_path(blob)
```

`resolve_blob_local_path()` is an inspection operation and may return `None`
for a remote provider. It never changes the durable provider-neutral URI.
