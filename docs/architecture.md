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
