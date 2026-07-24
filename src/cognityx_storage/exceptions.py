"""Errors raised through the provider-neutral storage API."""


class StorageError(Exception):
    """Base class for storage failures."""


class InvalidStorageKeyError(StorageError, ValueError):
    """A logical key is empty, unsafe, or not portable."""


class ObjectNotFoundError(StorageError, FileNotFoundError):
    """The requested logical key does not exist."""


class ObjectAlreadyExistsError(StorageError, FileExistsError):
    """Publication would replace an existing object."""


class UnsupportedOperationError(StorageError):
    """The selected backend cannot perform the requested operation."""

