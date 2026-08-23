#!/usr/bin/env python3
"""
labels.py -- class palettes and IoU maths. Pure numpy, no rclpy.

Deliberately free of ROS so the metric can be unit-tested on handmade PNGs
without a running graph (BUILD_SPEC.md section 8, test 2). scoring_node.py is a
thin ROS wrapper over this file; if a number looks wrong, the bug is here and
you can reproduce it in a plain python REPL.

WHY QUANTISE AT ALL
-------------------
Unity's label camera renders a flat colour per class. Anything that touches
those pixels on the way -- a resize, a JPEG encode -- moves them off the exact
palette value. Nearest-colour assignment tolerates that drift; an equality test
against the palette silently would not, and would report IoU 0 with no error
anywhere.

That tolerance is a safety net, not a licence: publish label images as raw or
PNG. JPEG on a label image bleeds colour across class boundaries and no
downstream code can undo it.
"""

from __future__ import annotations

import numpy as np

#: Default class palette: name -> RGB as Unity's replacement shader renders it.
#: Override with the `palette` parameter on scoring_node once Unity's real
#: shader colours are known -- do not edit this and expect it to propagate.
DEFAULT_PALETTE: dict[str, tuple[int, int, int]] = {
    "unlabelled": (0, 0, 0),
    "drivable": (0, 255, 0),
    "obstacle": (255, 0, 0),
    "sky": (0, 0, 255),
}

#: Classes that count toward the headline mean IoU. `unlabelled` is ignored by
#: definition (BUILD_SPEC section 5.5). `sky` is excluded because it is a large,
#: trivially separable region -- including it inflates the mean and hides
#: exactly the ground/obstacle degradation this rig exists to measure.
DEFAULT_SCORED: tuple[str, ...] = ("drivable", "obstacle")

UNLABELLED_ID = 0


class LabelSpace:
    """A palette plus which of its classes are scored.

    Holds the palette as an (N, 3) array so quantisation is one vectorised
    distance computation rather than a python loop over pixels.
    """

    def __init__(
        self,
        palette: dict[str, tuple[int, int, int]] | None = None,
        scored: tuple[str, ...] | list[str] | None = None,
        max_distance: float | None = 96.0,
    ) -> None:
        palette = dict(palette or DEFAULT_PALETTE)
        if "unlabelled" not in palette:
            # Every image needs somewhere to put pixels that match nothing.
            palette = {"unlabelled": (0, 0, 0), **palette}

        # `unlabelled` is pinned to id 0 so UNLABELLED_ID is a constant rather
        # than something every caller has to look up.
        names = ["unlabelled"] + [n for n in palette if n != "unlabelled"]
        self.names: list[str] = names
        self.colours = np.array([palette[n] for n in names], dtype=np.int16)
        self.id_of = {n: i for i, n in enumerate(names)}

        wanted = list(scored) if scored is not None else list(DEFAULT_SCORED)
        missing = [n for n in wanted if n not in self.id_of]
        if missing:
            raise ValueError(
                f"scored classes {missing} are not in the palette "
                f"{sorted(self.id_of)}"
            )
        self.scored_names: list[str] = wanted
        self.scored_ids: list[int] = [self.id_of[n] for n in wanted]

        # Pixels farther than this from every palette colour become unlabelled
        # instead of being forced into the least-wrong class. None disables the
        # check, which is what you want if the palette covers the whole image.
        self.max_distance = max_distance

    def __len__(self) -> int:
        return len(self.names)

    def quantise(self, img: np.ndarray) -> np.ndarray:
        """Map an image to class ids, shape (h, w), dtype uint8.

        Accepts either an RGB image (h, w, 3), which is matched to the nearest
        palette colour, or a single-channel image (h, w) / (h, w, 1), which is
        already class ids and is passed through with out-of-range values folded
        to unlabelled.
        """
        if img.ndim == 3 and img.shape[2] == 1:
            img = img[:, :, 0]

        if img.ndim == 2:
            ids = img.astype(np.uint8, copy=True)
            ids[ids >= len(self.names)] = UNLABELLED_ID
            return ids

        if img.ndim != 3 or img.shape[2] not in (3, 4):
            raise ValueError(
                f"expected (h, w), (h, w, 3) or (h, w, 4); got {img.shape}"
            )
        rgb = img[:, :, :3].astype(np.int16)

        # (h, w, N) squared distances to every palette colour at once. int16
        # inputs keep the subtraction exact; int32 keeps the square from
        # wrapping (3 * 255**2 = 195075, well past int16).
        diff = rgb[:, :, None, :] - self.colours[None, None, :, :]
        dist2 = np.einsum("hwnc,hwnc->hwn", diff, diff, dtype=np.int32)

        ids = np.argmin(dist2, axis=2).astype(np.uint8)
        if self.max_distance is not None:
            too_far = np.min(dist2, axis=2) > (self.max_distance ** 2)
            ids[too_far] = UNLABELLED_ID
        return ids


def iou_per_class(
    pred_ids: np.ndarray, gt_ids: np.ndarray, class_ids: list[int]
) -> dict[int, float | None]:
    """IoU for each class id. None means the class was absent from both images.

    An absent class has an empty union. Scoring it 0 punishes perception for a
    class it was never shown; scoring it 1 rewards it for the same. Both are
    wrong, so it is skipped and excluded from the mean (BUILD_SPEC 5.5).
    """
    if pred_ids.shape != gt_ids.shape:
        raise ValueError(
            f"shape mismatch: prediction {pred_ids.shape} vs "
            f"ground truth {gt_ids.shape} -- resize before scoring"
        )
    out: dict[int, float | None] = {}
    for cid in class_ids:
        p = pred_ids == cid
        g = gt_ids == cid
        inter = int(np.count_nonzero(p & g))
        union = int(np.count_nonzero(p | g))
        out[cid] = None if union == 0 else inter / union
    return out


def mean_iou(per_class: dict[int, float | None]) -> float | None:
    """Mean over the classes that were actually present. None if none were."""
    vals = [v for v in per_class.values() if v is not None]
    return float(np.mean(vals)) if vals else None


def score(
    pred_ids: np.ndarray, gt_ids: np.ndarray, space: LabelSpace
) -> tuple[float | None, dict]:
    """The whole metric: mean IoU plus the detail payload for /score/detail."""
    per_id = iou_per_class(pred_ids, gt_ids, space.scored_ids)
    detail = {
        "per_class": {
            space.names[cid]: (None if v is None else round(v, 5))
            for cid, v in per_id.items()
        },
        "pixels_gt": {
            space.names[cid]: int(np.count_nonzero(gt_ids == cid))
            for cid in space.scored_ids
        },
        "pixels_pred": {
            space.names[cid]: int(np.count_nonzero(pred_ids == cid))
            for cid in space.scored_ids
        },
        "skipped": [
            space.names[cid] for cid, v in per_id.items() if v is None
        ],
        "resolution": [int(gt_ids.shape[1]), int(gt_ids.shape[0])],
    }
    m = mean_iou(per_id)
    detail["mean_iou"] = None if m is None else round(m, 5)
    return m, detail
