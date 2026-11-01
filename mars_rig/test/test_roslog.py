"""Logging at a runtime-chosen severity, without tripping rclpy.

rclpy raises `ValueError: Logger severity cannot be changed between calls.` if
one call site (function, file, line) is used at two severities. The invariant
is a property of our own source, so these tests need no rclpy.
"""

import inspect

import pytest

from mars_rig.roslog import emit


class RecordingLogger:
    """Records each severity and the roslog.py line that called it."""

    def __init__(self):
        self.calls = []

    def _record(self, severity):
        # stack()[0] is here, [1] is the debug/info/... method below,
        # [2] is the line inside emit() that called it -- what rclpy sees.
        caller = inspect.stack()[2]
        self.calls.append((severity, caller.filename, caller.lineno))

    def debug(self, msg): self._record("debug")
    def info(self, msg): self._record("info")
    def warning(self, msg): self._record("warning")
    def error(self, msg): self._record("error")
    def fatal(self, msg): self._record("fatal")


@pytest.mark.parametrize("level,expected", [
    ("debug", "debug"),
    ("info", "info"),
    ("warn", "warning"),        # rclpy's warn() is deprecated in favour of warning()
    ("warning", "warning"),
    ("error", "error"),
    ("fatal", "fatal"),
    ("INFO", "info"),           # case-insensitive
    ("", "info"),               # empty falls back rather than raising
    (None, "info"),
])
def test_level_routes_to_the_right_method(level, expected):
    logger = RecordingLogger()
    emit(logger, level, "hello")
    assert [c[0] for c in logger.calls] == [expected]


def test_every_severity_comes_from_a_different_line():
    """No two severities may share a call site (as a dict lookup or getattr would)."""
    logger = RecordingLogger()
    for level in ("debug", "info", "warn", "error", "fatal"):
        emit(logger, level, "x")

    sites = {severity: (path, line) for severity, path, line in logger.calls}
    assert len(sites) == 5, "expected one site per severity"
    assert len(set(sites.values())) == 5, (
        f"two severities share a call site, which rclpy refuses: {sites}")


def test_repeated_calls_at_one_severity_share_a_site():
    """One site reused at a single severity is fine to rclpy."""
    logger = RecordingLogger()
    emit(logger, "warn", "a")
    emit(logger, "warn", "b")
    assert len({(c[1], c[2]) for c in logger.calls}) == 1


def test_emit_does_not_dispatch_dynamically():
    """Source-shape guard: the behavioural tests would also pass with getattr."""
    src = inspect.getsource(emit)
    body = src.split('"""')[-1]      # skip the docstring, which names them
    assert "getattr(" not in body
    assert "[level]" not in body
