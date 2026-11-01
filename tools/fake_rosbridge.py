#!/usr/bin/env python3
"""
fake_rosbridge.py -- drive panel/index.html with no ROS, no Unity, no credits.

Speaks just enough of the rosbridge v2 JSON protocol for the control panel:
subscribe, unsubscribe, advertise, publish. It then streams a synthetic scene
on the three raw image topics, a falling score on /score/iou, per-class detail,
director status and bridge state.

    python3 tools/fake_rosbridge.py            # ws://localhost:9090
    ./rig panel --demo                         # this plus the page server

WHY
---
The panel is a judged deliverable, and everything it displays arrives over a
websocket. Waiting for Unity and a live session before finding out whether it
renders is the wrong order: this lets the page be built, styled and rehearsed
against realistic traffic, and it makes a panel regression something you can
catch in seconds.

Every value it emits is synthetic and the panel labels it as such -- the state
payload carries mock:true, and the detail payload carries fake:true.
"""

import argparse
import asyncio
import base64
import json
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                os.pardir, "mars_rig"))

from mars_rig.labels import DEFAULT_PALETTE, LabelSpace, score  # noqa: E402
from mars_rig.perception import colourise, segment  # noqa: E402
from mars_rig.synthetic_scene import SyntheticScene, degrade  # noqa: E402

try:
    import websockets
except ImportError:
    sys.exit("needs `pip install websockets` -- or run ./rig install --offline-only")

CATALOG_IDS = ["B00", "S40", "L10", "A20", "A21", "A22", "A23", "X74"]


def image_msg(rgb):
    """A sensor_msgs/Image the way rosbridge puts one on the wire.

    Raw, not CompressedImage: the real graph publishes raw Image on these
    topics and the panel decodes raw, so sending JPEG here would rehearse a
    path that does not exist. `data` is base64 because that is what
    rosbridge_library does with a uint8[] -- the panel has to handle exactly
    that, and this is where it gets tested.
    """
    rgb = np.ascontiguousarray(rgb, dtype=np.uint8)
    h, w = rgb.shape[:2]
    return {
        "header": {"frame_id": "fake_cam"},
        "height": h, "width": w,
        "encoding": "rgb8", "is_bigendian": 0, "step": w * 3,
        "data": base64.b64encode(rgb.tobytes()).decode("ascii"),
    }


class FakeBridge:
    IMAGE_TOPICS = ("/unity/camera/image_raw", "/world_model/image_raw",
                    "/perception/mask")

    def __init__(self, fps=8.0, width=384, height=240, silent=False):
        self.fps = fps
        # --silent rehearses the failure this panel exists to make legible:
        # the graph is up, rosbridge is fine, and the image topics have no
        # publisher at all.
        self.silent = silent
        self.image_topics = () if silent else self.IMAGE_TOPICS
        self.space = LabelSpace(DEFAULT_PALETTE, scored=("drivable", "obstacle"))
        self.scene = SyntheticScene(width=width, height=height, seed=7)
        self.subscribed: set[str] = set()
        self.prompt = ""
        self.t = 0.0

    async def handler(self, ws):
        print(f"panel connected from {ws.remote_address}", flush=True)
        await ws.send(json.dumps({"op": "status", "level": "info",
                                  "msg": "fake_rosbridge: synthetic data only"}))
        pump = asyncio.create_task(self.pump(ws))
        try:
            async for raw in ws:
                await self.on_message(ws, raw)
        except Exception:
            pass
        finally:
            pump.cancel()
            print("panel disconnected", flush=True)

    async def on_message(self, ws, raw):
        try:
            m = json.loads(raw)
        except ValueError:
            return
        op = m.get("op")
        if op == "subscribe":
            self.subscribed.add(m["topic"])
            print(f"  subscribe {m['topic']}", flush=True)
        elif op == "unsubscribe":
            self.subscribed.discard(m["topic"])
        elif op == "advertise":
            print(f"  advertise {m['topic']}", flush=True)
        elif op == "call_service":
            # rosapi, as rosbridge_server serves it. The panel uses these two
            # to tell "wrong topic name" apart from "no frames" -- so the
            # rehearsal has to answer them, including the --silent case where
            # the honest answer is "nothing publishes that".
            await self.on_service(ws, m)
        elif op == "publish":
            # The panel publishes conditions. Echo it back through the state
            # topic so typing in the box visibly changes something, which is
            # exactly the demo interaction being rehearsed.
            self.prompt = (m.get("msg") or {}).get("data", "")
            print(f"  PROMPT ({len(self.prompt)} chars): "
                  f"{self.prompt[:60]}...", flush=True)

    async def on_service(self, ws, m):
        service = m.get("service", "")
        args = m.get("args") or {}
        values, result = {}, True
        if service.endswith("topics_for_type"):
            values = {"topics": sorted(self.image_topics)}
        elif service.endswith("publishers"):
            topic = args.get("topic", "")
            values = {"publishers": ["/fake_rosbridge"]
                      if topic in self.image_topics else []}
        else:
            result = False
        await ws.send(json.dumps({"op": "service_response", "id": m.get("id"),
                                  "service": service, "values": values,
                                  "result": result}))

    async def send(self, ws, topic, msg):
        if topic in self.subscribed:
            await ws.send(json.dumps({"op": "publish", "topic": topic,
                                      "msg": msg}))

    async def pump(self, ws):
        period = 1.0 / self.fps
        i = 0
        while True:
            self.t += period
            # Severity walks up and down so the score visibly moves.
            sev = 0.5 - 0.5 * math.cos(self.t / 6.0)
            unity_rgb, labels = self.scene.render(self.t)
            world_rgb = degrade(unity_rgb, sev, "compound", seed=i)
            ids = segment(world_rgb, self.space)
            mean, detail = score(ids, self.space.quantise(labels), self.space)

            if not self.silent:
                for topic, rgb in (
                    ("/unity/camera/image_raw", unity_rgb),
                    ("/world_model/image_raw", world_rgb),
                    ("/perception/mask", colourise(ids, self.space)),
                ):
                    await self.send(ws, topic, image_msg(rgb))

            if mean is not None:
                await self.send(ws, "/score/iou", {"data": float(mean)})
                detail["fake"] = True
                await self.send(ws, "/score/detail",
                                {"data": json.dumps(detail)})

            cid = CATALOG_IDS[int(self.t / 8) % len(CATALOG_IDS)]
            await self.send(ws, "/director/status", {"data": json.dumps({
                "mode": "adaptive", "phase": "dwell", "current_id": cid,
                "current_name": f"synthetic_{cid.lower()}",
                "difficulty": int(sev * 5), "elapsed": round(self.t % 8, 1),
                "completed": int(self.t / 8) % len(CATALOG_IDS),
                "total": len(CATALOG_IDS),
                "last_iou": round(mean, 4) if mean is not None else None,
            })})
            await self.send(ws, "/world_model/state", {"data": json.dumps({
                "generating": True, "mock": True, "keep_backlog": True,
                "width": unity_rgb.shape[1], "height": unity_rgb.shape[0],
                "prompt": self.prompt or None,
            })})

            i += 1
            await asyncio.sleep(period)


async def main_async(args):
    bridge = FakeBridge(fps=args.fps, width=args.width, height=args.height,
                        silent=args.silent)
    async with websockets.serve(bridge.handler, args.host, args.port,
                                max_size=None):
        print(f"fake rosbridge on ws://{args.host}:{args.port} "
              f"-- point the panel at it (Ctrl-C to stop)", flush=True)
        await asyncio.Future()


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", default="localhost")
    p.add_argument("--port", type=int, default=9090)
    p.add_argument("--fps", type=float, default=8.0)
    # Smaller than the rig's 832x512 on purpose: raw frames cross this socket
    # uncompressed and base64 adds a third again, so 3 topics x 8 fps at the
    # real size is ~30 MB/s of JSON for three 400px panes.
    p.add_argument("--width", type=int, default=384)
    p.add_argument("--height", type=int, default=240)
    p.add_argument("--silent", action="store_true",
                   help="publish everything EXCEPT images, and report no "
                        "publishers for them -- rehearses the blank-pane "
                        "failure and the panel's explanation of it")
    args = p.parse_args()
    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
