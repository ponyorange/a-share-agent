class SignalGraphError(Exception):
    """Base error for the signal graph package."""


class InvalidTickerError(SignalGraphError):
    """Raised when a ticker cannot be normalized."""


class InvalidMarketDataError(SignalGraphError):
    """Raised when prices or costs are invalid."""


class NonMonotonicTickError(SignalGraphError):
    """Raised when a logical clock moves backwards."""


class SnapshotError(SignalGraphError):
    """Base error for malformed snapshots."""


class SnapshotVersionError(SnapshotError):
    """Raised for unsupported snapshot versions."""


class PredictionError(SignalGraphError):
    """Base error for prediction lifecycle failures."""


class PredictionNotFoundError(PredictionError):
    """Raised when a prediction ID is unknown."""


class PredictionNotMatureError(PredictionError):
    """Raised when settlement is attempted before due_tick."""
