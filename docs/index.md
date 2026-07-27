# Cognityx Storage

`cognityx-storage` routes logical storage roles to configured profiles while
preserving the existing provider-neutral storage operations.

## Zero-config use

```python
from cognityx_storage import StorageRuntime

storage = StorageRuntime.load()
assets = storage.for_role("source_asset")
stored = assets.put_file("incoming/report.pdf", "/tmp/report.pdf")

print(stored.uri)
# storage://local-main/source-assets/incoming/report.pdf
```

The built-in setup uses the local filesystem provider. A service selects a
role, not a backend or physical root.

## Start here

- [Storage concepts](concepts.md)
- [Configuration](configuration.md)
- [Architecture](architecture.md)
- [Development](development.md)
