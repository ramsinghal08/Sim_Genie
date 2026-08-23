#!/usr/bin/env python3
"""
roslog.py -- log at a severity chosen at runtime, without tripping rclpy.

THE TRAP
--------
rclpy caches a logging context per CALL SITE and refuses to let the severity
change under it:

    ValueError: Logger severity cannot be changed between calls.

The call site is `CallerId(function_name, file_path, line_number, last_index)`,
built from the first frame outside rclpy (`_find_caller`). So this, which looks
entirely reasonable, is a bug:

    getattr(self.get_logger(), level)(msg)          # one line, every severity
    lvl = logger.info if ok else logger.warn        # one line, two severities
    lvl(message)

Every severity resolves to the same CallerId, the context keeps whichever
severity happened to go through first, and the next one at a different severity
raises -- from inside the logging call, killing the node.

It is a nasty one because it needs two different severities through one line to
show up, so it survives every short test and fires later. In this rig it killed
the director on its first warning, and the same pattern sat in the X2 bridge's
`generation_stopped` handler, which only runs during a live paid session.

THE FIX
-------
Give every severity its own physical line. That is all `emit` is.
"""

from __future__ import annotations

#: Aliases we accept, mapped to what we actually call.
_LEVELS = ("debug", "info", "warn", "warning", "error", "fatal")


def emit(logger, level: str, message: str) -> None:
    """Log `message` at `level`.

    Each branch is a separate line on purpose: that is what gives rclpy a
    distinct CallerId per severity. Do not collapse this into a dict lookup or
    a getattr -- that is precisely the bug this exists to avoid.
    """
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
