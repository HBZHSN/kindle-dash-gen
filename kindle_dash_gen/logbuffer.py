from __future__ import annotations

import logging
import threading
from collections import deque
from typing import Any

DEFAULT_CAPACITY = 2000

EXCLUDED_LOGGERS = ("werkzeug",)


class _ExcludeLoggers(logging.Filter):
    def __init__(self, names: tuple[str, ...]) -> None:
        super().__init__()
        self._names = names

    def filter(self, record: logging.LogRecord) -> bool:
        return not any(record.name == name or record.name.startswith(name + ".") for name in self._names)


class RingBufferLogHandler(logging.Handler):
    """Keep the most recent log records in memory for the web UI to poll."""

    def __init__(self, capacity: int = DEFAULT_CAPACITY) -> None:
        super().__init__()
        self._capacity = max(1, capacity)
        self._buffer: deque[dict[str, Any]] = deque(maxlen=self._capacity)
        self._lock = threading.Lock()
        self._seq = 0

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
        except Exception:
            self.handleError(record)
            return
        with self._lock:
            self._seq += 1
            self._buffer.append(
                {
                    "seq": self._seq,
                    "time": record.created,
                    "level": record.levelname,
                    "name": record.name,
                    "message": message,
                }
            )

    def snapshot(self, after: int | None = None, limit: int | None = None) -> dict[str, Any]:
        with self._lock:
            entries = list(self._buffer)
            last_seq = self._seq
        if after is not None:
            entries = [entry for entry in entries if entry["seq"] > after]
        if limit is not None and limit > 0:
            entries = entries[-limit:]
        return {"entries": entries, "last_seq": last_seq, "capacity": self._capacity}

    def clear(self) -> None:
        with self._lock:
            self._buffer.clear()


_handler: RingBufferLogHandler | None = None
_install_lock = threading.Lock()


def install_log_buffer(
    capacity: int = DEFAULT_CAPACITY, level: int = logging.INFO
) -> RingBufferLogHandler:
    """Attach a shared ring-buffer handler to the root logger (idempotent)."""
    global _handler
    with _install_lock:
        if _handler is not None:
            return _handler
        handler = RingBufferLogHandler(capacity)
        handler.setLevel(level)
        handler.setFormatter(logging.Formatter("%(message)s"))
        handler.addFilter(_ExcludeLoggers(EXCLUDED_LOGGERS))
        root = logging.getLogger()
        if root.level == logging.NOTSET or root.level > level:
            root.setLevel(level)
        root.addHandler(handler)
        _handler = handler
        return handler


def get_log_buffer() -> RingBufferLogHandler | None:
    return _handler
