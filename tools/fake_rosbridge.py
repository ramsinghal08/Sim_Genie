#!/usr/bin/env python3
"""Fake rosbridge server: drives panel/index.html with no ROS, Unity or credits.

Implements enough of rosbridge v2 (subscribe, unsubscribe, advertise, publish,
rosapi calls) to stream a synthetic scene, score, director status and bridge
state. All values are synthetic and flagged (mock:true / fake:true).

    python3 tools/fake_rosbridge.py     # ws://localhost:9090
    ./rig panel --demo                  # this plus the page server
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
    """A raw sensor_msgs/Image as rosbridge serialises it (base64 `data`)."""
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
        # silent: image topics have no publisher at all.
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
            # rosapi lookups the panel uses to tell "wrong topic" from "no frames".
            await self.on_service(ws, m)
        elif op == "publish":
            # Echoed back through the state topic.
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
            # Severity oscillates so the score moves.
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
    # Smaller than the rig's 832x512: raw base64 frames are bandwidth heavy.
    p.add_argument("--width", type=int, default=384)
    p.add_argument("--height", type=int, default=240)
    p.add_argument("--silent", action="store_true",
                   help="publish everything except images and report no "
                        "publishers for them (blank-pane case)")
    args = p.parse_args()
    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
