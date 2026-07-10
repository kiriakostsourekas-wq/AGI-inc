"""Persistence failures with stable meanings at the trust gateway boundary."""


class PersistenceError(RuntimeError):
    pass


class RecordNotFoundError(PersistenceError):
    pass


class ImmutableRecordError(PersistenceError):
    pass


class ConcurrentStateError(PersistenceError):
    pass


class GrantInvalidError(PersistenceError):
    pass


class GrantExpiredError(GrantInvalidError):
    pass


class GrantStaleError(GrantInvalidError):
    pass


class GrantReplayError(GrantInvalidError):
    pass


class SideEffectConflictError(PersistenceError):
    pass


class DuplicateReplacementError(PersistenceError):
    pass
