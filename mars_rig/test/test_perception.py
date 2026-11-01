"""The classical baseline segmenter and the loop it sits in.

These assert the downstream contract (output shape and encoding, and that the
score falls as the image degrades), not segmentation quality.
"""

import numpy as np
import pytest

from mars_rig.labels import DEFAULT_PALETTE, LabelSpace, score
from mars_rig.perception import colourise, segment
from mars_rig.synthetic_scene import SyntheticScene, degrade


@pytest.fixture
def space():
    return LabelSpace(DEFAULT_PALETTE, scored=("drivable", "obstacle"))


@pytest.fixture
def scene():
    return SyntheticScene(width=320, height=192, seed=7)


def test_output_is_class_ids_of_the_input_size(space, scene):
    rgb, _ = scene.render(0.0)
    ids = segment(rgb, space)
    assert ids.shape == rgb.shape[:2]
    assert ids.dtype == np.uint8
    assert set(np.unique(ids)) <= set(range(len(space)))


def test_sky_is_above_the_horizon_and_ground_below(space, scene):
    rgb, _ = scene.render(0.0)
    ids = segment(rgb, space, horizon_frac=0.25)
    cut = int(192 * 0.25)
    assert (ids[:cut] == space.id_of["sky"]).all()
    assert (ids[cut:] != space.id_of["sky"]).all()


def test_colourise_round_trips_through_the_palette(space, scene):
    """The palette-RGB mask must re-quantise to exactly the ids perception produced."""
    rgb, _ = scene.render(0.0)
    ids = segment(rgb, space)
    assert (space.quantise(colourise(ids, space)) == ids).all()


def test_a_rock_on_flat_ground_is_found(space):
    """Edges and shadow on otherwise flat ground are called an obstacle."""
    import cv2
    rgb = np.full((160, 240, 3), 130, np.uint8)
    rgb[:20] = 210                                   # a thin sky band
    rng = np.random.default_rng(0)
    rgb[20:] = np.clip(rgb[20:] + rng.normal(0, 2, rgb[20:].shape), 0,
                       255).astype(np.uint8)
    cv2.circle(rgb, (120, 100), 22, (70, 62, 55), -1)

    ids = segment(rgb, space, horizon_frac=0.125)
    assert ids[100, 120] == space.id_of["obstacle"]
    # And it does not decide the whole frame is rock.
    ground = ids[20:]
    found = np.count_nonzero(ground == space.id_of["obstacle"])
    assert 0 < found < ground.size * 0.25


def test_scene_rgb_and_labels_are_pixel_aligned(space, scene):
    """RGB and labels come from one geometry, so labelled obstacle pixels are
    darker than the ground in the RGB at the same coordinates."""
    rgb, labels = scene.render(3.0)
    gt = space.quantise(labels)
    obstacle = gt == space.id_of["obstacle"]
    assert obstacle.any(), "no rocks in frame -- pick another t"
    grey = rgb.mean(axis=2)
    ground = (gt == space.id_of["drivable"])
    assert grey[obstacle].mean() < grey[ground].mean()


def test_degradation_lowers_the_score(space, scene):
    """Degrading the image should lower IoU."""
    rgb, labels = scene.render(2.0)
    gt = space.quantise(labels)

    clean, _ = score(segment(rgb, space), gt, space)
    wrecked, _ = score(
        segment(degrade(rgb, 1.0, "compound"), space), gt, space)

    assert clean is not None and wrecked is not None
    assert wrecked < clean, f"clean {clean:.3f} vs degraded {wrecked:.3f}"


def test_degradation_never_touches_geometry(scene):
    """degrade() changes appearance only; the labels are unchanged."""
    rgb, labels = scene.render(1.0)
    before = labels.copy()
    degrade(rgb, 1.0, "compound")
    assert (labels == before).all()


def test_unknown_method_is_refused(space, scene):
    rgb, _ = scene.render(0.0)
    with pytest.raises(ValueError, match="unknown perception method"):
        segment(rgb, space, "segformer-b0")
