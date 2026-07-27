"""Errors raised through the provider-neutral storage API."""


class StorageError(Exception):
    """Base class for storage failures."""


class InvalidStorageKeyError(StorageError, ValueError):
    """A logical key is empty, unsafe, or not portable."""


class ObjectNotFoundError(StorageError, FileNotFoundError):
    """The requested logical key does not exist."""


class ObjectAlreadyExistsError(StorageError, FileExistsError):
    """Publication would replace an existing object."""


class ObjectConsistencyError(StorageError):
    """An immutable object exists with content different from the required value."""


class UnsupportedOperationError(StorageError):
    """The selected backend cannot perform the requested operation."""


class StorageConfigurationError(StorageError, ValueError):
    """Storage configuration cannot be parsed or used safely."""


class StorageProviderUnavailableError(StorageError):
    """No backend implementation is registered for a configured profile."""


class StorageRoleNotFoundError(StorageError, KeyError):
    """The requested logical storage role is not configured."""


class StorageRoleUnavailableError(StorageError):
    """No configured profile for a role has an available backend."""
