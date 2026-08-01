# Cognityx Storage

`cognityx-storage` routes logical storage roles to configured profiles while
preserving the existing provider-neutral storage operations.

In ordinary terms, an application says what kind of data it has, and Storage
decides where that data belongs. Applications do not choose physical folders.

```text
Ingest, DataForge, Training, and Inference
                    ↓ storage role
             Storage Runtime
                    ↓ profile routing
          configured storage provider
```

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

Immutable bytes can be stored without calculating hashes or CAS keys in the
calling service. Storage performs the SHA-256 hashing, chooses the
content-addressed storage (CAS) key, applies the configured duplicate-reuse
boundary, and returns a stable Blob reference:

```python
from cognityx_resource import ResourceContext

context = ResourceContext(tenant_id="acme", principal_id="alice")
blob = storage.blobs("source_asset").put_file(
    "/tmp/report.pdf",
    context=context,
)
```

## Start here

- [Storage concepts](concepts.md)
- [Blobs and CAS](blobs.md)
- [Configuration](configuration.md)
- [Architecture](architecture.md)
- [Development](development.md)

## Deletion And Future Auto-clean

Domain services first remove logical references. Storage's Blob garbage
collector then plans unreferenced candidates and rechecks them before physical
deletion. A future always-running Storage service will automate this same
reference-safe process under a retention policy. The scheduler and service
process are roadmap work; they are not hidden inside the current library.
