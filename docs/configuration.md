# Storage configuration

Storage uses one TOML configuration. It does not merge multiple files.

Selection order is:

1. an explicit `config_file`;
2. `COGNITYX_STORAGE_CONFIG`;
3. project `.cognityx/storage.toml`;
4. `$XDG_CONFIG_HOME/cognityx/storage.toml`, normally
   `~/.config/cognityx/storage.toml`;
5. the built-in local configuration.

An explicit user configuration path is also accepted by the Python API for
testing and controlled embedding.

## Built-in configuration

Zero-config creates the `local-main` filesystem profile using
`/mnt/d/AI/cognitive/cognityx-storage`. It defines these roles:

| Role | Namespace |
| --- | --- |
| `catalog` | `catalog` |
| `source_asset` | `source-assets` |
| `artifact` | `artifacts` |
| `dataset` | `datasets` |
| `model` | `models` |
| `cache` | `cache` |
| `temporary` | `temporary` |

## Enterprise-style example

Only filesystem profiles are executable today. Object and HDFS profiles below
are accepted configuration placeholders and will be reported as unavailable.

```toml
[storage]
default_profile = "local-main"

[storage.profiles.local-main]
type = "filesystem"
root = "/mnt/d/AI/cognitive/cognityx-storage"

[storage.profiles.nvme-cache]
type = "filesystem"
root = "/mnt/nvme/cognityx-cache"

[storage.profiles.primary-object]
type = "object"
provider = "s3"
endpoint = "https://object.company.example"
bucket = "cognityx"
credentials_ref = "env:COGNITYX_OBJECT_CREDENTIALS"

[storage.profiles.enterprise-hdfs]
type = "hdfs"
endpoint = "hdfs://namenode:8020"
root = "/cognityx"

[storage.roles.catalog]
profile = "local-main"
namespace = "catalog"
preferred_capabilities = [
  "native_path",
  "random_write",
  "file_locking",
]

[storage.roles.source_asset]
profile = "primary-object"
fallback_profiles = ["local-main"]
namespace = "source-assets"
preferred_capabilities = [
  "stream_read",
  "stream_write",
  "distributed",
  "object_metadata",
]

[storage.roles.artifact]
profile = "primary-object"
fallback_profiles = ["local-main"]
namespace = "artifacts"

[storage.roles.dataset]
profile = "enterprise-hdfs"
fallback_profiles = ["primary-object", "local-main"]
namespace = "datasets"
preferred_capabilities = [
  "stream_read",
  "stream_write",
  "large_sequential_io",
]

[storage.roles.model]
profile = "primary-object"
fallback_profiles = ["local-main"]
namespace = "models"

[storage.roles.cache]
profile = "nvme-cache"
fallback_profiles = ["local-main"]
namespace = "cache"

[storage.roles.temporary]
profile = "local-main"
namespace = "temporary"
```

`credentials_ref` remains opaque. This release does not load credentials or
implement remote providers. Structured descriptions redact credential, token,
password, secret, and key-like option values.

## Validation

```python
from cognityx_storage import StorageConfig

config = StorageConfig.load(config_file=".cognityx/storage.toml")
report = config.validate()

print(report.errors)
print(report.warnings)
print(config.describe())
```

Malformed structure, unknown references, unsafe namespaces, missing filesystem
roots, and unknown capability names are errors. Provider unavailability and
preferred capability mismatches are warnings.
