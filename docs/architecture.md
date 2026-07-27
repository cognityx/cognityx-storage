# Architecture and extension

The runtime adds configuration and routing above the existing storage client:

```text
StorageRole
    ↓
StorageRoleResolver
    ↓
StorageProfile
    ↓
StorageBackendFactory
    ↓
StorageClient
```

The role-resolved store delegates I/O to `StorageClient`; it does not implement
a second storage engine.

## Configuration discovery

`StorageConfig.load()` selects one TOML file using explicit, environment,
project, user, then built-in precedence. Parsing creates immutable profile and
role values. Validation returns structured errors and warnings.

Structural ambiguity is fatal to `StorageRuntime.from_config()`. An unavailable
provider is not fatal because another role or fallback may remain usable.

## Provider registry

`StorageBackendFactory` is a small explicit registry. The built-in registry has
one entry for `filesystem`. There is no plugin discovery and no fake object or
HDFS implementation.

A future provider registers a builder and the capabilities its implementation
actually supplies:

```python
factory.register(
    "object",
    build_object_backend,
    capabilities=object_capabilities,
)
```

Expected capability templates used to understand configuration remain
separate from this available implementation signature.

## Role fallback

The resolver checks the primary profile and then configured fallbacks. It binds
the role to the first profile whose provider is registered. That binding is
used for all operations on the returned store.

Object-not-found does not trigger another profile lookup. Cross-profile
discovery, copying, migration, replication, and tiering are outside this
runtime.

## URI identity

The new runtime API returns:

```text
storage://<profile-name>/<role-namespace>/<logical-key>
```

The stable profile name is the Cognityx storage identity; filesystem, object,
or HDFS technology is implementation.

The existing low-level `StorageClient.uri()` deliberately remains
`storage://<scope>/<logical-key>`. Ingest still relies on that form. Migration
to role-based URIs will be explicit in a later job.

## Blob and CAS flow

```text
ResourceContext
    ↓
role dedup_scope
    ↓
non-identifying dedup domain
    ↓
incremental SHA-256
    ↓
immutable CAS publication
    ↓
BlobRef
```

The role-relative CAS key is:

```text
blob-domains/<domain>/sha256/<first-2>/<next-2>/<full-digest>
```

The resolved role namespace is prepended exactly once. `blob_id` is derived
from the stable profile name and full logical storage key. Provider technology,
filename, media type, project, workspace, and caller resource identifiers do
not participate in physical Blob identity.

Tenant, principal, Context, and system domains use SHA-derived tokens rather
than raw governance names. A `none` policy creates a unique `instance-...`
domain for each write.

There is no central Blob registry. The CAS object provides physical identity,
and the calling domain service persists the returned `BlobRef`.

Concurrent writers rely on backend no-overwrite publication. A losing writer
verifies the winning immutable object's size and digest before returning an
equivalent reference. Inconsistent existing content raises
`ObjectConsistencyError` and is never overwritten.

## Durable Blob reads

Blob creation follows current role routing and records the profile that
actually accepted the object. Later reads use the recorded `profile_name` and
`storage_key`, even if the role now resolves elsewhere. Missing content never
causes fallback-profile searching.

File and stream inputs share one snapshot pipeline. Storage opens a caller file
once, copies it to temporary storage while calculating SHA-256, and publishes
that exact staged snapshot. Non-seekable streams use the same bounded-memory
pipeline. This guarantees that the Blob digest and size describe the bytes
actually published even if the caller's original file changes. Temporary
content is cleaned after both successful and failed operations.

`PreparedBlob` exposes this unpublished stage to domain services that must
decide whether to accept content before durable publication. It owns the
temporary lifecycle and exposes only a provider-neutral `ContentDigest`.
Publication still delegates to the same BlobStore CAS path, so callers cannot
construct physical keys, dedup domains or Blob IDs. This is a small
prepare/commit boundary, not a general storage transaction framework.

## Native paths

`native_path(key)` returns the safe filesystem target for a role and key,
including when a SQLite-style target has not been created. It does not create
the file or parent directory.

`resolve_local_path(key)` instead locates an already-existing native
representation without materialization. Remote providers may return `None`
from `resolve_local_path`; requesting `native_path` from a provider that lacks
native filesystem semantics raises `UnsupportedOperationError`.

## Safety boundary

Logical keys reject absolute paths, parent traversal, control characters, and
platform-specific separators. Scope construction keeps user and shared data
under separate namespaces. The local backend is not an authorization system;
identity, policy, audit, and encryption belong to a future service boundary.
