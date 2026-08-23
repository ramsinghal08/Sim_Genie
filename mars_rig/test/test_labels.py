"""IoU maths, checked against numbers worked out by hand.

BUILD_SPEC.md section 8, test 2. These run with no ROS: `pytest mars_rig/test`.
"""

import numpy as np
import pytest

from mars_rig.labels import (
    DEFAULT_PALETTE, LabelSpace, iou_per_class, mean_iou, score,
)


@pytest.fixture
def space():
    return LabelSpace(DEFAULT_PALETTE, scored=("drivable", "obstacle"))


def _rgb(space, layout):
    """Build an RGB label image from a grid of class names."""
    h, w = len(layout), len(layout[0])
    img = np.zeros((h, w, 3), dtype=np.uint8)
    for y, row in enumerate(layout):
        for x, name in enumerate(row):
            img[y, x] = space.colours[space.id_of[name]]
    return img


def test_iou_matches_hand_calculation(space):
    # 4x4. Ground truth: two columns drivable, two columns obstacle.
    # Prediction:        three columns drivable, one column obstacle.
    # drivable: intersection 8 px, union 12 px -> 2/3
    # obstacle: intersection 4 px, union  8 px -> 1/2
    # mean = 7/12 = 0.58333...
    gt = np.zeros((4, 4, 3), np.uint8)
    gt[:, :2] = (0, 255, 0)
    gt[:, 2:] = (255, 0, 0)
    pred = np.zeros((4, 4, 3), np.uint8)
    pred[:, :3] = (0, 255, 0)
    pred[:, 3:] = (255, 0, 0)

    m, detail = score(space.quantise(pred), space.quantise(gt), space)
    assert detail["per_class"]["drivable"] == pytest.approx(2 / 3, abs=1e-4)
    assert detail["per_class"]["obstacle"] == pytest.approx(0.5, abs=1e-4)
    assert m == pytest.approx(7 / 12, abs=1e-4)
    assert detail["pixels_gt"] == {"drivable": 8, "obstacle": 8}
    assert detail["pixels_pred"] == {"drivable": 12, "obstacle": 4}


def test_perfect_prediction_is_one(space):
    gt = _rgb(space, [["drivable", "obstacle"], ["sky", "drivable"]])
    ids = space.quantise(gt)
    m, _ = score(ids, ids, space)
    assert m == pytest.approx(1.0)


def test_disjoint_prediction_is_zero(space):
    gt = np.full((4, 4, 3), 0, np.uint8)
    gt[:, :] = (0, 255, 0)          # all drivable
    pred = np.full((4, 4, 3), 0, np.uint8)
    pred[:, :] = (255, 0, 0)        # all obstacle
    m, detail = score(space.quantise(pred), space.quantise(gt), space)
    assert detail["per_class"]["drivable"] == 0.0
    assert detail["per_class"]["obstacle"] == 0.0
    assert m == 0.0


def test_absent_class_is_skipped_not_scored(space):
    """A class in neither image has an empty union. 0 punishes perception for a
    class it was never shown; 1 rewards it for the same. Both are wrong."""
    ids = np.zeros((4, 4), np.uint8)
    ids[:] = space.id_of["drivable"]
    per = iou_per_class(ids, ids, space.scored_ids)
    assert per[space.id_of["obstacle"]] is None
    assert per[space.id_of["drivable"]] == 1.0
    assert mean_iou(per) == pytest.approx(1.0)

    _, detail = score(ids, ids, space)
    assert detail["skipped"] == ["obstacle"]


def test_all_classes_absent_gives_none(space):
    ids = np.zeros((4, 4), np.uint8)   # everything unlabelled
    per = iou_per_class(ids, ids, space.scored_ids)
    assert mean_iou(per) is None
    m, _ = score(ids, ids, space)
    assert m is None


def test_quantise_tolerates_compression_drift(space):
    """Nearest-colour, not equality: a resize or an encode moves a flat label
    colour off its exact palette value and equality would silently score 0."""
    img = np.zeros((2, 2, 3), np.uint8)
    img[:] = (12, 240, 9)            # nearly (0, 255, 0)
    assert (space.quantise(img) == space.id_of["drivable"]).all()


def test_quantise_rejects_colours_that_match_nothing(space):
    """Beyond max_distance a pixel becomes unlabelled rather than being forced
    into the least-wrong class."""
    tight = LabelSpace(DEFAULT_PALETTE, scored=("drivable",), max_distance=20.0)
    img = np.zeros((2, 2, 3), np.uint8)
    img[:] = (128, 128, 128)
    assert (tight.quantise(img) == 0).all()


def test_quantise_accepts_single_channel_ids(space):
    ids = np.array([[0, 1], [2, 250]], np.uint8)
    out = space.quantise(ids)
    assert out[0, 0] == 0 and out[0, 1] == 1 and out[1, 0] == 2
    assert out[1, 1] == 0          # out of range folds to unlabelled


def test_shape_mismatch_is_an_error_not_a_number(space):
    a = np.zeros((4, 4), np.uint8)
    b = np.zeros((8, 8), np.uint8)
    with pytest.raises(ValueError, match="shape mismatch"):
        iou_per_class(a, b, space.scored_ids)


def test_unknown_scored_class_is_rejected():
    with pytest.raises(ValueError, match="not in the palette"):
        LabelSpace(DEFAULT_PALETTE, scored=("drivable", "lava"))
