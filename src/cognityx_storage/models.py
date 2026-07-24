"""Small provider-neutral values returned by the storage client."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class StoredObject:
    """Describe content stored behind a logical Cognityx key."""

    key: str
    uri: str
    size_bytes: int
    media_type: str
    is_directory: bool = False

