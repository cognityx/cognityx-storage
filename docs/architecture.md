# Architecture

The storage boundary has three intentionally small parts:

- `StorageClient` exposes the application-facing operations.
- Scoped clients separate user data from shared platform data.
- `LocalStorageBackend` maps validated logical keys to the configured local root.

Storage treats documents, extracted text, chunks, metadata, checkpoints, and
model artifacts as opaque content. A vector index or other service may retain
references to stable logical keys without knowing the physical backend.

## Safety boundary

Logical keys reject absolute paths, parent traversal, control characters, and
platform-specific separators. Scope construction keeps user and shared data
under separate namespaces. The local backend is not an authorization system;
identity, policy, audit, and encryption belong to a future service boundary.
