#!/usr/bin/env python3
"""imgcodec.py: sensor_msgs/Image <-> numpy without cv_bridge (pure numpy).

cv_bridge resolves OpenCV type codes from a compiled module but looks them up
in a table built from whatever `cv2` is imported at runtime. With mismatched
OpenCV versions (e.g. a pip OpenCV 5 over ROS's OpenCV 4) this raises
KeyError mid-publish, so 8-bit images are converted here instead.

Handles rgb8, bgr8, rgba8, bgra8, mono8 and the 8UC1/8UC3/8UC4 aliases, with
row padding (`step` > width * channels). 8-bit only; other depths are refused.
"""

from __future__ import annotations

import numpy as np

#: encoding -> channel count. The 8UCn aliases carry no colour order and are
#: never reordered.
CHANNELS = {
    "mono8": 1, "8UC1": 1,
    "rgb8": 3, "bgr8": 3, "8UC3": 3,
    "rgba8": 4, "bgra8": 4, "8UC4": 4,
}

_BGR_ORDER = {"bgr8", "bgra8"}


class UnsupportedEncoding(ValueError):
    """Raised for encodings this module will not guess at."""


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

    # Strip per-row padding; reshaping directly would shear the image.
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

    A colour image yields its first channel, not a luminance mix: callers pass
    class-id maps widened to three identical channels.
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
# Duck-typed on the message fields so this module imports without ROS.

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
    """CompressedImage -> contiguous (h, w, 3) RGB via cv2.imdecode."""
    import cv2
    buf = np.frombuffer(msg.data, dtype=np.uint8)
    bgr = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    if bgr is None:
        raise ValueError(f"could not decode {msg.format!r} payload "
                         f"({buf.size} bytes)")
    return np.ascontiguousarray(bgr[:, :, ::-1])


# ------------------------------------------------------------------- QoS
#
# A BEST_EFFORT publisher and a RELIABLE subscriber never connect in ROS 2,
# silently. rosbridge_server and web_video_server subscribe RELIABLE, so image
# publishers here offer RELIABLE and image subscribers request BEST_EFFORT,
# which connects to any peer, including sensor-data publishers.

def image_pub_qos(depth: int = 2):
    """RELIABLE, shallow, VOLATILE: readable by any subscriber."""
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
    """BEST_EFFORT, shallow: accepts frames from any publisher."""
    from rclpy.qos import (
        DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy,
    )
    return QoSProfile(
        depth=depth,
        history=HistoryPolicy.KEEP_LAST,
        reliability=ReliabilityPolicy.BEST_EFFORT,
        durability=DurabilityPolicy.VOLATILE,
    )
