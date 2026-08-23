#!/usr/bin/env python3
"""
imgcodec.py -- sensor_msgs/Image <-> numpy, without cv_bridge. Pure numpy.

WHY NOT cv_bridge
-----------------
cv_bridge answers "what OpenCV type is rgb8?" from its COMPILED module, built
against the OpenCV that ROS ships, and then looks that answer up in a table it
builds at runtime from whatever `import cv2` happens to give it. When those two
are different OpenCV versions, the lookup misses and it raises deep inside a
publish call:

    KeyError: 16

OpenCV 5 renumbered the type constants -- CV_8UC3 is 64 there and 16 in
OpenCV 4 -- so a pip-installed OpenCV 5 shadowing ROS's OpenCV 4 is enough to
take the bridge down mid-run. Nothing in the traceback says "you have two
OpenCVs", and `pip install ultralytics` is enough to cause it, which is a
plausible thing for someone to do here while deciding BUILD_SPEC 9.2.

We move 8-bit images in four encodings. That is a handful of numpy reshapes,
and doing it here means no version of anything can produce that failure again.

WHAT IT HANDLES
---------------
rgb8, bgr8, rgba8, bgra8, mono8, and the 8UC1/8UC3/8UC4 aliases. Row padding
(`step` larger than width * channels) is respected. 8-bit only: nothing in this
rig moves 16-bit or float images, and quietly half-supporting them would be
worse than refusing.
"""

from __future__ import annotations

import numpy as np

#: encoding -> channel count. The 8UCn aliases carry no colour order, so they
#: are taken at face value and never reordered.
CHANNELS = {
    "mono8": 1, "8UC1": 1,
    "rgb8": 3, "bgr8": 3, "8UC3": 3,
    "rgba8": 4, "bgra8": 4, "8UC4": 4,
}

_BGR_ORDER = {"bgr8", "bgra8"}


class UnsupportedEncoding(ValueError):
    """Raised rather than guessed. A wrong guess here is silent garbage."""


def channels_of(encoding: str) -> int:
    try:
        return CHANNELS[encoding]
    except KeyError:
        raise UnsupportedEncoding(
            f"encoding {encoding!r} is not 8-bit image data this rig moves; "
            f"known: {', '.join(sorted(CHANNELS))}"
        ) from None


def decode(data, height: int, width: int, step: int, encoding: str) -> np.ndarray:
    """Message bytes -> array, in the encoding's own channel order.

    Returns (h, w) for single-channel and (h, w, C) otherwise.
    """
    c = channels_of(encoding)
    row = width * c
    step = int(step) or row
    if step < row:
        raise ValueError(
            f"step {step} is smaller than one row of {width}x{c} -- the header "
            f"and the payload disagree about the image")

    buf = np.frombuffer(data, dtype=np.uint8)
    if buf.size < step * height:
        raise ValueError(
            f"payload is {buf.size} bytes, need {step * height} for "
            f"{width}x{height} {encoding}")

    # Slice the padding off each row rather than assuming it is absent: a
    # publisher that aligns rows produces a step larger than width*channels,
    # and reshaping straight to (h, w, c) would shear the image diagonally
    # instead of failing.
    out = buf[:step * height].reshape(height, step)[:, :row]
    return out.reshape(height, width, c) if c > 1 else out.reshape(height, width)


def as_rgb(array: np.ndarray, encoding: str) -> np.ndarray:
    """Whatever came in -> contiguous (h, w, 3) RGB."""
    c = channels_of(encoding)
    if c == 1:
        arr = array if array.ndim == 2 else array[:, :, 0]
        return np.repeat(arr[:, :, None], 3, axis=2)
    rgb = array[:, :, :3]
    if encoding in _BGR_ORDER:
        rgb = rgb[:, :, ::-1]
    return np.ascontiguousarray(rgb)


def as_single_channel(array: np.ndarray, encoding: str) -> np.ndarray:
    """Whatever came in -> (h, w) uint8.

    For a colour image this takes the FIRST channel, not a luminance mix: the
    only colour images asked for this are class-id maps that were widened to
    three identical channels, and averaging them would turn an id of 2 into
    something that is not an id at all.
    """
    if array.ndim == 2:
        return array
    return np.ascontiguousarray(array[:, :, 0])


def encode(array: np.ndarray, encoding: str) -> tuple[bytes, int]:
    """Array -> (payload bytes, step), ready for an Image message."""
    c = channels_of(encoding)
    if array.dtype != np.uint8:
        raise UnsupportedEncoding(
            f"{encoding} is 8-bit; got dtype {array.dtype}")

    if c == 1:
        if array.ndim == 3:
            array = array[:, :, 0]
        if array.ndim != 2:
            raise ValueError(f"{encoding} wants (h, w); got {array.shape}")
        height, width = array.shape
    else:
        if array.ndim != 3 or array.shape[2] != c:
            raise ValueError(
                f"{encoding} wants (h, w, {c}); got {array.shape}")
        height, width = array.shape[:2]
        if encoding in _BGR_ORDER:
            array = array[:, :, ::-1]

    return np.ascontiguousarray(array).tobytes(), width * c


# ---------------------------------------------------------------- messages
#
# Duck-typed on the fields, so this file still imports with no ROS present and
# stays testable against a plain object. sensor_msgs/Image is just five fields
# and a payload; nothing here needs the class itself.

def msg_to_array(msg) -> np.ndarray:
    """Image message -> array in its own channel order."""
    return decode(msg.data, msg.height, msg.width, msg.step, msg.encoding)


def msg_to_rgb(msg) -> np.ndarray:
    """Image message -> contiguous (h, w, 3) RGB."""
    return as_rgb(msg_to_array(msg), msg.encoding)


def msg_to_single_channel(msg) -> np.ndarray:
    """Image message -> (h, w) uint8, first channel for colour input."""
    return as_single_channel(msg_to_array(msg), msg.encoding)


def fill_msg(msg, array: np.ndarray, encoding: str):
    """Populate an Image message from an array. Returns the message."""
    data, step = encode(array, encoding)
    msg.height = int(array.shape[0])
    msg.width = int(array.shape[1])
    msg.encoding = encoding
    msg.is_bigendian = 0
    msg.step = step
    msg.data = data
    return msg


def compressed_to_rgb(msg) -> np.ndarray:
    """CompressedImage -> contiguous (h, w, 3) RGB, via OpenCV's own decoder.

    Safe across OpenCV versions in a way cv_bridge is not: imencode/imdecode
    are pure cv2, so whichever cv2 is imported is self-consistent. The version
    trap is only in cv_bridge's compiled-vs-runtime type table.
    """
    import cv2
    buf = np.frombuffer(msg.data, dtype=np.uint8)
    bgr = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    if bgr is None:
        raise ValueError(f"could not decode {msg.format!r} payload "
                         f"({buf.size} bytes)")
    return np.ascontiguousarray(bgr[:, :, ::-1])


# ------------------------------------------------------------------- QoS
#
# Kept here because every node that touches an image needs the same two
# profiles, and getting them the wrong way round is invisible.
#
# THE TRAP: a BEST_EFFORT publisher and a RELIABLE subscriber are INCOMPATIBLE
# in ROS 2 -- the subscriber demands a guarantee the publisher does not offer,
# so the two never connect. rclpy logs nothing at default severity, `ros2 topic
# hz` on the publisher still reports 24, and the panel shows an empty box.
#
# rosbridge_server and web_video_server both subscribe RELIABLE by default and
# neither exposes a QoS override worth relying on. So every image publisher
# here offers RELIABLE (a publisher may always offer more than a subscriber
# asks for) and every image subscriber requests BEST_EFFORT. That pair
# connects to everything, in both directions, including sensor-data publishers
# like the Unity endpoint.

def image_pub_qos(depth: int = 2):
    """RELIABLE, shallow, VOLATILE -- readable by any subscriber."""
    from rclpy.qos import (
        DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy,
    )
    return QoSProfile(
        depth=depth,
        history=HistoryPolicy.KEEP_LAST,
        reliability=ReliabilityPolicy.RELIABLE,
        durability=DurabilityPolicy.VOLATILE,
    )


def image_sub_qos(depth: int = 2):
    """BEST_EFFORT, shallow -- accepts frames from any publisher."""
    from rclpy.qos import (
        DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy,
    )
    return QoSProfile(
        depth=depth,
        history=HistoryPolicy.KEEP_LAST,
        reliability=ReliabilityPolicy.BEST_EFFORT,
        durability=DurabilityPolicy.VOLATILE,
    )
