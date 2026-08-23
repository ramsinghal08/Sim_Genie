#!/usr/bin/env python3
"""
synthetic_scene.py -- a stand-in Unity, for rehearsing the loop without one.

WHAT THIS IS FOR
----------------
Unity is the longest pole (BUILD_SPEC.md 5.1). Everything downstream of it --
perception, the scorer, the director, the panel -- can be built and exercised
before it exists, provided something produces an RGB frame and a pixel-aligned
label frame. That is all this does.

WHAT IT IS NOT
--------------
It is not a measurement, and `degrade()` is not a model of X2. It applies a
crude appearance change so the offline loop produces a curve with the right
SHAPE, which is enough to prove the plumbing and to rehearse the demo. Every
number that comes out of it is fiction and is labelled as such wherever it is
written down. Real numbers need Unity and a live session.

The one property it does share with the real thing: the RGB and the labels are
generated from the same geometry in the same call, so they are pixel-identical
by construction -- which is exactly the property BUILD_SPEC 5.1 says to verify
in Unity before trusting anything downstream.
"""

from __future__ import annotations

import numpy as np

try:
    import cv2
    CV2_AVAILABLE = True
except ImportError:  # pragma: no cover
    CV2_AVAILABLE = False

from mars_rig.labels import DEFAULT_PALETTE, LabelSpace

#: Which crude appearance change stands in for each catalog family. The letters
#: are the catalog's own (B baseline, L illumination, A atmosphere, E exposure,
#: S surface, N sensor, T terrain colour, X compound).
FAMILY_STYLE = {
    "B": "none", "L": "lowsun", "A": "dust", "E": "glare",
    "S": "flatten", "N": "sensor", "T": "recolour", "X": "compound",
}

#: Per-condition overrides, where one family covers two unlike failures.
#: Family N is "sensor degradation", but a monochrome navcam and a heavily
#: compressed downlink break an image in completely different ways -- one
#: removes colour and leaves structure intact, the other shreds structure and
#: leaves colour alone. Treating them as one style made monochrome the worst
#: difficulty-2 condition in the whole ladder, which is an artefact of this
#: file and not a claim about anything.
CONDITION_STYLE = {
    "N52": "mono",       # monochrome navcam: desaturate, nothing else
    "N51": "compress",   # bandwidth-limited downlink: real JPEG blocking
}


def style_for(prompt: dict) -> str:
    """The stand-in style for a catalog entry: override first, then family."""
    return CONDITION_STYLE.get(prompt.get("id"),
                               FAMILY_STYLE.get(prompt.get("family"), "dust"))


class SyntheticScene:
    """A rock field the camera drives through on a fixed, repeatable path.

    Fixed path and fixed seed on purpose: identical motion every run is what
    makes a score change attributable to the visual condition alone
    (BUILD_SPEC 5.1).
    """

    def __init__(self, width=640, height=384, n_rocks=14, seed=7,
                 speed=1.4, horizon_frac=0.25,
                 palette=None):
        if not CV2_AVAILABLE:
            raise RuntimeError("synthetic_scene needs cv2")
        self.w, self.h = int(width), int(height)
        self.horizon = int(self.h * horizon_frac)
        self.f = self.w * 0.8
        self.cam_h = 1.1
        self.speed = float(speed)
        self.space = LabelSpace(palette or DEFAULT_PALETTE,
                                scored=("drivable", "obstacle"))

        rng = np.random.default_rng(seed)
        # (lateral offset, initial depth, radius) in metres.
        self.rocks = np.stack([
            rng.uniform(-6.0, 6.0, n_rocks),
            rng.uniform(4.0, 60.0, n_rocks),
            rng.uniform(0.18, 0.75, n_rocks),
        ], axis=1)
        self.loop_m = 60.0     # depth at which a passed rock is recycled
        self._noise = rng.normal(0.0, 1.0, (self.h, self.w)).astype(np.float32)

    # ---------- rendering ----------

    def render(self, t: float):
        """(rgb, label_rgb) for time t. Both from the same geometry."""
        rgb = self._ground_and_sky()
        labels = self._label_base()

        travelled = self.speed * t
        for x_m, d0, r_m in self.rocks:
            depth = d0 - travelled
            # Recycle rather than despawn, so rock count stays constant and the
            # scene never runs empty on a long sweep.
            depth = ((depth - 1.5) % self.loop_m) + 1.5
            u = int(round(self.w / 2 + self.f * x_m / depth))
            v = int(round(self.horizon + self.f * self.cam_h / depth))
            r = max(1, int(round(self.f * r_m / depth)))
            if v - r > self.h or u + r < 0 or u - r > self.w:
                continue
            self._draw_rock(rgb, labels, u, v, r, depth)
        return rgb, labels

    def _ground_and_sky(self):
        img = np.zeros((self.h, self.w, 3), dtype=np.float32)
        rows = np.arange(self.h, dtype=np.float32)[:, None]

        sky = np.clip(0.55 + 0.35 * (rows / max(self.horizon, 1)), 0, 1)
        img[:self.horizon] = (sky[:self.horizon] * 200.0)[..., None] * \
            np.array([1.02, 0.96, 0.90], np.float32)

        # Ground brightens toward the camera and carries fine grain, which is
        # what the texture-based segmenter keys off.
        depth_fade = np.clip(
            (rows[self.horizon:] - self.horizon) / max(self.h - self.horizon, 1),
            0, 1)
        base = 96.0 + 52.0 * depth_fade
        img[self.horizon:] = base[..., None] * \
            np.array([1.0, 0.88, 0.78], np.float32)
        img[self.horizon:] += (self._noise[self.horizon:] * 5.0)[..., None]
        return np.clip(img, 0, 255).astype(np.uint8)

    def _label_base(self):
        lab = np.zeros((self.h, self.w, 3), dtype=np.uint8)
        lab[:self.horizon] = self.space.colours[self.space.id_of["sky"]]
        lab[self.horizon:] = self.space.colours[self.space.id_of["drivable"]]
        return lab

    def _draw_rock(self, rgb, labels, u, v, r, depth):
        centre = (u, v - int(r * 0.55))
        axes = (r, int(r * 0.78))

        # A contact shadow in RGB only. It is appearance, not geometry, so it
        # must NOT appear in the labels -- putting it there would hand
        # perception credit for finding a shadow instead of a rock. Kept soft
        # and tight: Martian light is diffuse, and an exaggerated shadow just
        # manufactures false positives that say more about this file than about
        # the segmenter.
        shadow = np.zeros_like(rgb)
        cv2.ellipse(shadow, (u, v), (max(1, int(r * 1.05)), max(1, int(r * 0.28))),
                    0, 0, 360, (1, 1, 1), -1)
        m = shadow[:, :, 0].astype(bool)
        rgb[m] = (rgb[m].astype(np.float32) * 0.72).astype(np.uint8)

        shade = int(np.clip(150 - 55 * np.exp(-depth / 25.0), 60, 180))
        cv2.ellipse(rgb, centre, axes, 0, 0, 360,
                    (shade, int(shade * 0.86), int(shade * 0.75)), -1)
        cv2.ellipse(rgb, centre, axes, 0, 200, 340,
                    (max(0, shade - 45),) * 3, -1)

        cv2.ellipse(labels, centre, axes, 0, 0, 360,
                    tuple(int(c) for c in
                          self.space.colours[self.space.id_of["obstacle"]]), -1)


def degrade(rgb: np.ndarray, severity: float, style: str = "dust",
            seed: int = 0) -> np.ndarray:
    """Crude appearance change standing in for an X2 repaint.

    severity is 0..1. Geometry is never touched: this is exactly the boundary
    the catalog prompts try to hold X2 to, so the stand-in holds it too.
    """
    s = float(np.clip(severity, 0.0, 1.0))
    if style == "none" or s <= 0:
        return rgb
    img = rgb.astype(np.float32)
    rng = np.random.default_rng(seed)

    if style in ("dust", "compound"):
        # Airlight: what reaches the camera is the scene attenuated by
        # transmission t, plus the veil weighted by (1 - t). Transmission decays
        # with optical depth, so the useful curve is exponential rather than
        # linear -- gentle for thin haze, collapsing for a whiteout.
        #
        # The linear version this replaces scaled contrast by 0.42 at the top of
        # the ladder, which left every rock edge comfortably above the
        # segmenter's threshold. That made A23 whiteout, a difficulty 4
        # condition whose whole premise is near-total information loss, score
        # 0.635 against a 0.698 baseline -- i.e. harmless.
        transmission = float(np.exp(-4.0 * s * s))
        veil = np.array([196, 158, 118], np.float32)
        img = img * transmission + veil * (1.0 - transmission)
    if style in ("lowsun", "compound"):
        img *= (1 - 0.55 * s)
        # A raking gradient across the frame, brightest on one side.
        ramp = np.linspace(1.0 + 0.5 * s, 1.0 - 0.5 * s, img.shape[1],
                           dtype=np.float32)
        img *= ramp[None, :, None]
    if style in ("glare", "compound"):
        h, w = img.shape[:2]
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        d = np.sqrt((xx - w * 0.72) ** 2 + (yy - h * 0.18) ** 2)
        img += (255.0 * s * np.exp(-d / (0.45 * w)))[..., None]
    if style in ("flatten", "compound"):
        mean = img.mean(axis=(0, 1), keepdims=True)
        img = mean + (img - mean) * (1 - 0.6 * s)
        if CV2_AVAILABLE and s > 0.3:
            img = cv2.GaussianBlur(img, (0, 0), 1.0 + 3.0 * s)
    if style in ("sensor", "compound"):
        img += rng.normal(0.0, 14.0 * s, img.shape).astype(np.float32)
        grey = img.mean(axis=2, keepdims=True)
        img = img * (1 - 0.85 * s) + grey * (0.85 * s)
    if style in ("mono", "compound"):
        # Desaturation only. Note this barely moves a luminance-based segmenter,
        # which converts to grey as its first step anyway -- and that is the
        # honest answer for one, not a bug. It is exactly what the catalog entry
        # sets out to test: how much the model leans on colour over structure.
        img = np.repeat(img.mean(axis=2, keepdims=True), 3, axis=2)
    if style in ("compress", "compound") and CV2_AVAILABLE:
        # A real JPEG round trip, not gaussian noise: what a bandwidth-limited
        # downlink does is 8x8 blocking and ringing along edges, and a segmenter
        # keyed on local variance responds to that very differently than it does
        # to white noise.
        quality = int(max(2, min(95, round(95.0 * np.exp(-3.0 * s)))))
        bgr = cv2.cvtColor(np.clip(img, 0, 255).astype(np.uint8),
                           cv2.COLOR_RGB2BGR)
        ok, buf = cv2.imencode(".jpg", bgr,
                               [int(cv2.IMWRITE_JPEG_QUALITY), quality])
        if ok:
            img = cv2.cvtColor(cv2.imdecode(buf, cv2.IMREAD_COLOR),
                               cv2.COLOR_BGR2RGB).astype(np.float32)
    if style == "recolour":
        img = img * np.array([1.0 - 0.35 * s, 1.0, 1.0 + 0.25 * s], np.float32)

    return np.clip(img, 0, 255).astype(np.uint8)


def severity_for(difficulty: int) -> float:
    """Catalog difficulty 0..5 -> severity 0..1."""
    return float(np.clip(difficulty, 0, 5)) / 5.0
