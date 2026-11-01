#!/usr/bin/env python3
"""perception.py: the segmentation step (numpy/cv2, no rclpy).

`baseline_cv` is a classical traversability segmenter (horizon split, then
texture and shadow within the ground band) that stands in for a learned model
such as SegFormer-B0 or DeepLabV3-MobileNetV3. `segment()` dispatches on a
method name so another model can be added as one more branch. Being classical,
it degrades under washed-out contrast, low sun and flattened texture, which
gives a meaningful baseline failure curve.
"""

from __future__ import annotations

import numpy as np

try:
    import cv2
    CV2_AVAILABLE = True
except ImportError:  # pragma: no cover
    CV2_AVAILABLE = False

from mars_rig.labels import LabelSpace


def segment(rgb: np.ndarray, space: LabelSpace, method: str = "baseline_cv",
            **kw) -> np.ndarray:
    """RGB image -> class ids, shape (h, w), uint8. Never sees ground truth."""
    if method == "baseline_cv":
        return baseline_cv(rgb, space, **kw)
    if method == "passthrough":
        # Treat the input as an existing label map (for wiring tests).
        return space.quantise(rgb)
    raise ValueError(
        f"unknown perception method {method!r}; known: baseline_cv, passthrough"
    )


def baseline_cv(
    rgb: np.ndarray,
    space: LabelSpace,
    horizon_frac: float = 0.25,
    texture_ksize: int = 9,
    texture_thresh: float = 6.0,
    shadow_offset: float = 22.0,
    min_blob_px: int = 24,
    close_ksize: int = 3,
    fill_blobs: bool = True,
) -> np.ndarray:
    """Horizon split, then texture + shadow inside the ground band.

    horizon_frac    fraction of the frame from the top taken as sky (ground
                    fills ~75% with the current camera pitch; re-tune if it
                    changes).
    texture_thresh  local standard deviation above which ground is called an
                    obstacle.
    shadow_offset   how far below the local mean a pixel must fall to count as
                    a shadow, in grey levels.
    close_ksize     morphological closing applied before filling, to join an
                    edge response broken by a highlight.
    fill_blobs      fill each detected outline; the variance detector fires on
                    rock edges, not flat interiors.
    """
    if not CV2_AVAILABLE:
        raise RuntimeError("baseline_cv needs cv2 -- pip install opencv-python")

    h, w = rgb.shape[:2]
    ids = np.zeros((h, w), dtype=np.uint8)
    grey = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)

    sky_id = space.id_of.get("sky")
    drivable_id = space.id_of.get("drivable")
    obstacle_id = space.id_of.get("obstacle")

    horizon = int(round(h * float(horizon_frac)))
    horizon = max(0, min(h, horizon))
    if sky_id is not None:
        ids[:horizon, :] = sky_id

    ground = grey[horizon:, :]
    if ground.size == 0:
        return ids

    k = int(texture_ksize) | 1  # cv2 box filters want an odd kernel

    # Local std via E[x^2] - E[x]^2 (two box filters, cheap on CPU).
    f = ground.astype(np.float32)
    mean = cv2.blur(f, (k, k))
    mean_sq = cv2.blur(f * f, (k, k))
    var = np.maximum(mean_sq - mean * mean, 0.0)
    std = np.sqrt(var)

    obstacle = (std > float(texture_thresh)) | (f < (mean - float(shadow_offset)))

    k_close = int(close_ksize) | 1
    if k_close > 1:
        obstacle = cv2.morphologyEx(
            obstacle.astype(np.uint8), cv2.MORPH_CLOSE,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k_close, k_close))
        ).astype(bool)

    if fill_blobs:
        obstacle = _fill_outlines(obstacle)

    # Drop noise speckle so it does not inflate the predicted obstacle area.
    obstacle = _drop_small_blobs(obstacle, int(min_blob_px))

    band = ids[horizon:, :]
    if drivable_id is not None:
        band[:] = drivable_id
    if obstacle_id is not None:
        band[obstacle] = obstacle_id
    ids[horizon:, :] = band
    return ids


def _fill_outlines(mask: np.ndarray) -> np.ndarray:
    """Fill every external contour, so an outline becomes the object."""
    contours, _ = cv2.findContours(
        mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return mask
    filled = np.zeros(mask.shape, dtype=np.uint8)
    cv2.drawContours(filled, contours, -1, 1, thickness=cv2.FILLED)
    return filled.astype(bool)


def _drop_small_blobs(mask: np.ndarray, min_px: int) -> np.ndarray:
    if min_px <= 1:
        return mask
    n, labels, stats, _ = cv2.connectedComponentsWithStats(
        mask.astype(np.uint8), connectivity=8)
    keep = np.zeros(n, dtype=bool)
    for i in range(1, n):
        keep[i] = stats[i, cv2.CC_STAT_AREA] >= min_px
    return keep[labels]


def colourise(ids: np.ndarray, space: LabelSpace) -> np.ndarray:
    """Class ids -> palette RGB (viewable, and re-quantises to the same ids)."""
    return space.colours[ids].astype(np.uint8)
