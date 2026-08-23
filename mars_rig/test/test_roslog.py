"""Logging at a runtime-chosen severity, without tripping rclpy.

rclpy keys its logging context on CallerId(function_name, file_path,
line_number, last_index), taken from the first frame outside rclpy, and raises
`ValueError: Logger severity cannot be changed between calls.` if one call site
is ever used at two severities. That killed the director on its first warning.

These tests do not need rclpy: the invariant is a property of our own source,
and it is checkable directly.
"""

import inspect

import pytest

from mars_rig.roslog import emit


class RecordingLogger:
    """Records the severity AND the line in roslog.py that called it.

    The line number is the whole point: it is the field rclpy uses to tell one
    logging call site from another.
    """

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
    """The invariant rclpy actually enforces. If two severities ever share a
    line, the second one raises inside the logging call and takes the node
    down -- which is what a dict lookup or getattr would reintroduce."""
    logger = RecordingLogger()
    for level in ("debug", "info", "warn", "error", "fatal"):
        emit(logger, level, "x")

    sites = {severity: (path, line) for severity, path, line in logger.calls}
    assert len(sites) == 5, "expected one site per severity"
    assert len(set(sites.values())) == 5, (
        f"two severities share a call site, which rclpy refuses: {sites}")


def test_repeated_calls_at_one_severity_share_a_site():
    """The complement: rclpy is fine with one site reused at ONE severity, and
    that is the common case. This guards against someone 'fixing' the above by
    scattering severities across lines dynamically."""
    logger = RecordingLogger()
    emit(logger, "warn", "a")
    emit(logger, "warn", "b")
    assert len({(c[1], c[2]) for c in logger.calls}) == 1


def test_emit_does_not_dispatch_dynamically():
    """A regression guard on the shape of the source, because the behavioural
    tests above pass just as happily with getattr -- the failure only appears
    once a real rclpy logger is on the other end."""
    src = inspect.getsource(emit)
    body = src.split('"""')[-1]      # skip the docstring, which names them
    assert "getattr(" not in body
    assert "[level]" not in body
