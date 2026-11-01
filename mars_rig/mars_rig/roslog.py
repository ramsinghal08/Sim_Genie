#!/usr/bin/env python3
"""roslog.py: log at a runtime-chosen severity without tripping rclpy.

rclpy caches a logging context per call site (function, file, line) and raises
`ValueError: Logger severity cannot be changed between calls` if a second
severity goes through the same line, e.g. `getattr(logger, level)(msg)`. That
fires on the first mismatched severity, killing the node.

`emit` gives each severity its own physical line. Do not collapse it into a
dict lookup or getattr.
"""

from __future__ import annotations

#: Accepted level names.
_LEVELS = ("debug", "info", "warn", "warning", "error", "fatal")


def emit(logger, level: str, message: str) -> None:
    """Log `message` at `level`, one call site per severity."""
    level = (level or "info").lower()
    if level == "debug":
        logger.debug(message)
    elif level in ("warn", "warning"):
        logger.warning(message)
    elif level == "error":
        logger.error(message)
    elif level == "fatal":
        logger.fatal(message)
    else:
        logger.info(message)
