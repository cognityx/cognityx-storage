# Cognityx Storage

`cognityx-storage` gives Cognityx services a small, provider-neutral storage
API. Services work with logical keys and scopes instead of physical filesystem
paths or cloud SDKs.

The initial backend is local filesystem storage. Future backends can preserve
the same application-facing boundary while changing where bytes live.

## Start here

- [Architecture](architecture.md)
- [Development](development.md)
