"""Small capability signatures for configured storage profiles."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class StorageCapabilities:
    """Describe storage semantics a backend implementation can actually provide."""

    stream_read: bool = False
    stream_write: bool = False
    native_path: bool = False
    random_write: bool = False
    file_locking: bool = False
    hierarchical_namespace: bool = False
    range_read: bool = False
    object_metadata: bool = False
    distributed: bool = False
    large_sequential_io: bool = False

    @classmethod
    def names(cls) -> frozenset[str]:
        """Return the capability names accepted in configuration."""
        return frozenset(cls.__dataclass_fields__)

    def supports(self, name: str) -> bool:
        """Return whether one known capability is available."""
        if name not in self.names():
            raise ValueError(f"Unknown storage capability: {name}")
        return bool(getattr(self, name))

    def missing(self, names: tuple[str, ...]) -> tuple[str, ...]:
        """Return requested capabilities that this signature does not provide."""
        return tuple(name for name in names if not self.supports(name))

    def to_dict(self) -> dict[str, bool]:
        """Return a structured representation suitable for diagnostics."""
        return asdict(self)


FILESYSTEM_CAPABILITIES = StorageCapabilities(
    stream_read=True,
    stream_write=True,
    native_path=True,
    random_write=True,
    file_locking=True,
    hierarchical_namespace=True,
    large_sequential_io=True,
)

OBJECT_EXPECTED_CAPABILITIES = StorageCapabilities(
    stream_read=True,
    stream_write=True,
    range_read=True,
    object_metadata=True,
    distributed=True,
    large_sequential_io=True,
)

HDFS_EXPECTED_CAPABILITIES = StorageCapabilities(
    stream_read=True,
    stream_write=True,
    hierarchical_namespace=True,
    range_read=True,
    distributed=True,
    large_sequential_io=True,
)

_EXPECTED_BY_TYPE = {
    "filesystem": FILESYSTEM_CAPABILITIES,
    "object": OBJECT_EXPECTED_CAPABILITIES,
    "hdfs": HDFS_EXPECTED_CAPABILITIES,
}


def expected_capabilities(profile_type: str) -> StorageCapabilities:
    """Return the declared template for a recognized profile type."""
    return _EXPECTED_BY_TYPE.get(profile_type, StorageCapabilities())


def known_profile_types() -> frozenset[str]:
    """Return profile types understood by configuration."""
    return frozenset(_EXPECTED_BY_TYPE)
