"""Everything this package raises."""


class NotifyError(Exception):
    """Base for every error a caller should expect."""


class ConfigError(NotifyError):
    """The config is missing, unreadable or malformed."""


class DeliveryError(NotifyError):
    """The notification could not be delivered."""
