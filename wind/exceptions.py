"""

    wind.exceptions
    ~~~~~~~~~~~~~~~

    Exceptions

"""


class WindException(Exception):
    """Base exception class for ``wind``"""
    pass


class ServerError(WindException):
    """Server error occured"""
    pass


class SocketError(ServerError):
    """Socket error occured"""
    pass


class PollError(WindException):
    """Poll error occured"""
    pass


class LooperError(WindException):
    """Looper error occured"""
    pass


class StreamError(WindException):
    """Stream error occured"""
    pass


class ApplicationError(WindException):
    """Application error occured"""
    pass


class HTTPError(WindException):
    """Error for HTTP abnormal status handling"""
    pass


class LoggerError(WindException):
    """Logger error occured"""
    pass
