"""Image <-> numpy without cv_bridge (which raises KeyError on mixed OpenCV versions)."""

import numpy as np
import pytest

from mars_rig.imgcodec import (
    UnsupportedEncoding, as_rgb, as_single_channel, channels_of, decode, encode,
)


def test_round_trip_rgb8():
    img = np.arange(4 * 5 * 3, dtype=np.uint8).reshape(4, 5, 3)
    data, step = encode(img, "rgb8")
    assert step == 15
    assert (decode(data, 4, 5, step, "rgb8") == img).all()


def test_round_trip_mono8():
    img = np.arange(12, dtype=np.uint8).reshape(3, 4)
    data, step = encode(img, "mono8")
    assert step == 4
    assert (decode(data, 3, 4, step, "mono8") == img).all()


def test_bgr8_is_reordered_both_ways():
    rgb = np.zeros((1, 1, 3), np.uint8)
    rgb[0, 0] = (10, 20, 30)
    data, step = encode(rgb, "bgr8")
    assert tuple(np.frombuffer(data, np.uint8)) == (30, 20, 10)
    assert (as_rgb(decode(data, 1, 1, step, "bgr8"), "bgr8") == rgb).all()


def test_row_padding_is_sliced_not_reshaped():
    """Row padding (step > width*channels) must be stripped, not sheared."""
    width, height, pad = 3, 2, 5
    step = width * 3 + pad
    rows = [bytes([1, 1, 1, 2, 2, 2, 3, 3, 3]) + b"\xff" * pad,
            bytes([4, 4, 4, 5, 5, 5, 6, 6, 6]) + b"\xff" * pad]
    out = decode(b"".join(rows), height, width, step, "rgb8")
    assert out.shape == (2, 3, 3)
    assert [int(v) for v in out[:, :, 0].ravel()] == [1, 2, 3, 4, 5, 6]
    assert 255 not in out, "padding leaked into the image"


def test_rgba_drops_alpha_and_bgra_reorders():
    bgra = np.zeros((1, 1, 4), np.uint8)
    bgra[0, 0] = (30, 20, 10, 255)
    assert tuple(as_rgb(bgra, "bgra8")[0, 0]) == (10, 20, 30)
    rgba = np.zeros((1, 1, 4), np.uint8)
    rgba[0, 0] = (10, 20, 30, 255)
    assert tuple(as_rgb(rgba, "rgba8")[0, 0]) == (10, 20, 30)


def test_mono_widens_to_three_identical_channels():
    grey = np.array([[7, 8]], np.uint8)
    out = as_rgb(grey, "mono8")
    assert out.shape == (1, 2, 3)
    assert (out[0, 0] == 7).all() and (out[0, 1] == 8).all()


def test_single_channel_takes_the_first_channel_not_a_luminance_mix():
    """Class ids widened to three channels must come back unchanged (no luminance mix)."""
    ids = np.zeros((1, 2, 3), np.uint8)
    ids[0, 0] = (2, 2, 2)
    ids[0, 1] = (1, 1, 1)
    assert [int(v) for v in as_single_channel(ids, "8UC3").ravel()] == [2, 1]


def test_truncated_payload_is_an_error_not_a_crop():
    with pytest.raises(ValueError, match="payload is"):
        decode(b"\x00" * 10, 4, 5, 15, "rgb8")


def test_step_smaller_than_a_row_is_an_error():
    with pytest.raises(ValueError, match="step"):
        decode(b"\x00" * 100, 4, 5, 4, "rgb8")


def test_unknown_encoding_is_refused_not_guessed():
    with pytest.raises(UnsupportedEncoding):
        channels_of("32FC1")
    with pytest.raises(UnsupportedEncoding):
        encode(np.zeros((2, 2), np.uint16), "mono8")


def test_shape_must_match_the_encoding():
    with pytest.raises(ValueError):
        encode(np.zeros((2, 2, 3), np.uint8), "rgba8")


class FakeImageMsg:
    """Stands in for sensor_msgs/Image."""
    height = width = step = 0
    encoding = ""
    is_bigendian = 0
    data = b""


def test_msg_helpers_round_trip():
    from mars_rig.imgcodec import fill_msg, msg_to_rgb, msg_to_single_channel

    img = np.arange(2 * 3 * 3, dtype=np.uint8).reshape(2, 3, 3)
    msg = fill_msg(FakeImageMsg(), img, "rgb8")
    assert (msg.height, msg.width, msg.step) == (2, 3, 9)
    assert msg.encoding == "rgb8" and msg.is_bigendian == 0
    assert (msg_to_rgb(msg) == img).all()

    ids = np.array([[0, 1, 2], [2, 1, 0]], np.uint8)
    m2 = fill_msg(FakeImageMsg(), ids, "mono8")
    assert (msg_to_single_channel(m2) == ids).all()


def test_a_label_mask_survives_the_topic_unchanged():
    """The scorer quantises the mask back to class ids, so transport must not alter colours."""
    from mars_rig.imgcodec import fill_msg, msg_to_rgb
    from mars_rig.labels import DEFAULT_PALETTE, LabelSpace
    from mars_rig.perception import colourise

    space = LabelSpace(DEFAULT_PALETTE, scored=("drivable", "obstacle"))
    ids = np.array([[0, 1], [2, 3]], np.uint8)
    msg = fill_msg(FakeImageMsg(), colourise(ids, space), "rgb8")
    assert (space.quantise(msg_to_rgb(msg)) == ids).all()
