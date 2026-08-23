#!/usr/bin/env python3
"""
offline_loop.py -- run the whole rig with no ROS, no Unity and no credits.

    synthetic scene -> stand-in restyle -> perception -> scorer -> director
                              ^                                       |
                              +------------- next prompt -------------+

WHY THIS EXISTS
---------------
Four people share one paid session and Unity is the longest pole, so the loop
has to be provable before either is available. This runs the SAME perception,
the SAME IoU maths and the SAME director core that the ROS nodes run -- only
the transport (topics) and the two ends (Unity, X2) are stood in for.

WHAT IT PROVES AND WHAT IT DOES NOT
-----------------------------------
Proves:      the loop closes, the ladder walks, results.csv and results.md
             populate, IoU responds to a degrading image, settle_sec earns its
             keep (see --block-sec below).
Proves NOT:  any number. synthetic_scene.degrade() is not X2. The results this
             writes are stamped SIMULATED and must never be shown to a judge as
             a measurement.

USAGE
-----
    python3 tools/offline_loop.py --ceiling             # the sanity check first
    python3 tools/offline_loop.py --mode sweep --settle 2 --dwell 4
    python3 tools/offline_loop.py --mode adaptive --settle 2 --dwell 4
"""

import argparse
import os
import sys
import time

# Import the real package whether or not it has been colcon-installed.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                os.pardir, "mars_rig"))

import numpy as np  # noqa: E402

from mars_rig.director_core import Catalog, DirectorCore  # noqa: E402
from mars_rig.labels import DEFAULT_PALETTE, LabelSpace, score  # noqa: E402
from mars_rig.perception import segment  # noqa: E402
from mars_rig.synthetic_scene import (  # noqa: E402
    SyntheticScene, degrade, severity_for, style_for,
)

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CATALOG = os.path.join(
    HERE, os.pardir, "mars_rig", "config", "prompt_catalog_x2.json")


def log(level, msg):
    print(f"[{level:5}] {msg}", flush=True)


def ceiling(scene, space, args):
    """Score perception on the PLAIN render against its own labels.

    BUILD_SPEC 5.5: this is the ceiling. If it is low, perception or label
    alignment is broken and no world-model result below it means anything.
    """
    vals = []
    for i in range(args.ceiling_frames):
        rgb, labels = scene.render(i / args.fps)
        pred = segment(rgb, space, args.method,
                       horizon_frac=args.horizon_frac)
        m, detail = score(pred, space.quantise(labels), space)
        if m is not None:
            vals.append(m)
    if not vals:
        log("error", "no scored class present -- palette mismatch")
        return None
    log("info", f"CEILING mean IoU {np.mean(vals):.3f} "
                f"(min {min(vals):.3f} max {max(vals):.3f}, n={len(vals)}) "
                f"per-class {detail['per_class']}")
    return float(np.mean(vals))


def run(args):
    space = LabelSpace(DEFAULT_PALETTE, scored=("drivable", "obstacle"))
    scene = SyntheticScene(width=args.width, height=args.height, seed=args.seed)

    if args.ceiling:
        ceiling(scene, space, args)
        if args.ceiling_only:
            return 0

    emitted = []          # (time, prompt) the director asked X2 to apply
    applied = None        # what the stand-in model is actually rendering
    t = [0.0]             # simulated clock, in a list so the emit closure sees it

    catalog = Catalog(args.catalog)
    core = DirectorCore(
        catalog, mode=args.mode, lang=args.lang,
        settle_sec=args.settle, dwell_sec=args.dwell, out_dir=args.out,
        escalate_above=args.escalate_above, break_below=args.break_below,
        emit=lambda text, prompt: emitted.append((t[0], prompt)),
        log=log,
    )
    dt = 1.0 / args.fps
    wall0 = time.monotonic()

    while core.phase != "done" and t[0] < args.max_sec:
        # A prompt does not take effect the instant it is sent: X2 applies it at
        # the next block boundary. Modelling that lag here is what makes the
        # settle period visible instead of theoretical.
        while emitted and (t[0] - emitted[0][0]) >= args.block_sec:
            applied = emitted.pop(0)[1]

        rgb, labels = scene.render(t[0])
        if applied is not None:
            rgb = degrade(rgb, severity_for(applied["difficulty"]),
                          style_for(applied), seed=int(t[0] * 1000))

        pred = segment(rgb, space, args.method, horizon_frac=args.horizon_frac)
        m, detail = score(pred, space.quantise(labels), space)
        if m is not None:
            core.add_score(m)
            core.add_detail(detail)

        core.tick(t[0])
        t[0] += dt
        if args.real_time:
            time.sleep(max(0.0, (t[0] - (time.monotonic() - wall0))))

    if core.phase != "done":
        log("warn", f"stopped at max_sec={args.max_sec} in phase "
                    f"{core.phase} after {len(core.results)} conditions")
        core.write()

    _stamp_simulated(os.path.join(args.out, "results.md"))
    log("info", f"wrote {args.out}/results.csv and {args.out}/results.md "
                f"({len(core.results)} conditions, {t[0]:.0f}s simulated)")
    return 0


def _stamp_simulated(path):
    """Anything this harness writes says so, in the file, at the top.

    A results.md that looks exactly like a real one is a genuine hazard at
    16:00 on Sunday.
    """
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        body = fh.read()
    banner = (
        "> **SIMULATED — NOT A MEASUREMENT.** Produced by "
        "`tools/offline_loop.py` against a synthetic scene and a stand-in for "
        "X2. Proves the loop, not the numbers.\n\n"
    )
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(banner + body)


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--catalog", default=os.path.normpath(DEFAULT_CATALOG))
    p.add_argument("--mode", default="sweep",
                   choices=("sweep", "adaptive"))
    p.add_argument("--lang", default="zh")
    p.add_argument("--settle", type=float, default=2.0)
    p.add_argument("--dwell", type=float, default=4.0)
    p.add_argument("--block-sec", type=float, default=1.5,
                   help="simulated lag before a prompt takes effect")
    p.add_argument("--fps", type=float, default=6.0,
                   help="simulated scoring rate; the real scorer caps at 10 Hz")
    p.add_argument("--out", default="results/offline")
    p.add_argument("--width", type=int, default=640)
    p.add_argument("--height", type=int, default=384)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--method", default="baseline_cv")
    p.add_argument("--horizon-frac", type=float, default=0.25)
    p.add_argument("--escalate-above", type=float, default=0.75)
    p.add_argument("--break-below", type=float, default=0.25)
    p.add_argument("--max-sec", type=float, default=400.0,
                   help="simulated-seconds budget; a stalled adaptive run "
                        "hits this instead of spinning forever")
    p.add_argument("--real-time", action="store_true",
                   help="pace to the wall clock instead of running flat out")
    p.add_argument("--ceiling", action="store_true",
                   help="measure the ceiling (plain render vs its own labels) first")
    p.add_argument("--ceiling-only", action="store_true")
    p.add_argument("--ceiling-frames", type=int, default=12)
    args = p.parse_args()
    if args.ceiling_only:
        args.ceiling = True
    os.makedirs(args.out, exist_ok=True)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
