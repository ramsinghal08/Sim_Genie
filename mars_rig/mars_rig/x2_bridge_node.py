#!/usr/bin/env python3
"""
x2_bridge_node.py -- ROS 2 <-> Reactor X2 bridge.

X2 is a CONDITIONAL VIDEO model, not a generator. It consumes an inbound video
track and returns an edited one. That is why this is fundamentally simpler than
the Helios bridge:

    Unity frames  --> [source track]  --> X2 --> [main_video] --> ROS 2

Structure comes from the source on EVERY frame, so there is no seed decay and
no re-anchoring. The drift curve we measured on Helios does not apply.

TOPICS
------
Subscribes:
    <input_image_topic>       sensor_msgs/Image    Unity's frame, raw rgb8
                              (set input_compressed:=true for a CompressedImage
                              publisher on <input_image_topic>/compressed)
    /world_model/prompt       std_msgs/String      from prompt_director.py

Publishes:
    /world_model/image_raw            sensor_msgs/Image
    /world_model/camera_info          sensor_msgs/CameraInfo
    /world_model/state                std_msgs/String   JSON session state

    /world_model/image_raw/compressed sensor_msgs/CompressedImage
                              ONLY when publish_compressed:=true. Off by
                              default: nothing in this rig subscribes to it
                              (the panel and web_video_server both read the raw
                              topic), and a JPEG encode per frame is not free.

RUN
---
    export REACTOR_API_KEY=rk_...
    ros2 run mars_rig x2_bridge_node --ros-args \
        -p input_image_topic:=/unity/camera/image_raw \
        -p keep_backlog:=true

    # no API, no credits, for everyone else on the team:
    ros2 run mars_rig x2_bridge_node --ros-args -p mock:=true

KEEP_BACKLOG -- read this before your scoring runs
--------------------------------------------------
    false (default)  newest frames, drops backlog, bounded latency.
                     Right for a LIVE Unity feed.
    true             every frame in order, smoother, growing delay.
                     Right for a PRE-RECORDED clip and for reproducible
                     scoring runs, where you want every source frame edited.

Use true for the results table. Use false for the live demo.

MEDIA API -- verified against reactor-sdk 1.1.1
------------------------------------------------------------------------
    help(Reactor.publish_track) ->  async publish_track(self, name: 'str') -> 'Track'

The SDK owns the media path itself; there is no aiortc and no `av` anywhere in
it (reactor-sdk declares no runtime dependencies at all). So:

    src = await reactor.publish_track("source")   # a NAME, never a track object
    src.push_frame(rgb)                           # SYNCHRONOUS, (h, w, 3) uint8 RGB

`push_frame` is sync and does no pacing, so _pump_source() below owns the 24 fps
clock. Inbound, `track.on_frame(fn)` hands back an RGB (h, w, 3) array -- the
BGRA-to-RGB conversion happens inside the SDK.

The earlier version of this file passed an aiortc VideoStreamTrack and tried
three call shapes to find the signature. None of them can work: the first
argument is encoded as a track name, so a track object raises AttributeError on
`name.encode()` before any of the fallbacks are reached.
"""

import asyncio
import json
import os
import threading
import time
from collections import deque

import cv2
import numpy as np
import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from sensor_msgs.msg import CameraInfo, CompressedImage, Image
from std_msgs.msg import String

from mars_rig.imgcodec import (
    compressed_to_rgb, fill_msg, image_pub_qos, image_sub_qos, msg_to_rgb,
)
from mars_rig.roslog import emit
from mars_rig.topicwatch import TopicWatchdog

try:
    from reactor_sdk import Reactor
    REACTOR_AVAILABLE = True
except ImportError:
    REACTOR_AVAILABLE = False

SOURCE_FPS = 24          # X2's native source pacing. Do not change.


class X2Bridge(Node):
    def __init__(self):
        super().__init__("x2_bridge")

        self.declare_parameter("model_name", "x2")
        self.declare_parameter("input_image_topic", "/unity/camera/image_raw")
        # RAW BY DEFAULT. Subscribing to <topic>/compressed when the publisher
        # only offers raw connects nothing, reports nothing, and leaves the
        # bridge pushing its grey placeholder at X2 forever -- so this follows
        # what Unity's DualCapture actually publishes, which is raw Image.
        # Flip to true only alongside a real CompressedImage publisher; raw
        # rgb8 at 832x512x24fps is ~30 MiB/s, so drop rgbFps or the resolution
        # instead of assuming the link will carry it.
        self.declare_parameter("input_compressed", False)
        self.declare_parameter("initial_prompt", "")
        self.declare_parameter("keep_backlog", True)
        self.declare_parameter("publish_fps", 24.0)
        self.declare_parameter("frame_id", "x2_cam")
        # Nothing subscribes to the compressed companion topic. Opt in.
        self.declare_parameter("publish_compressed", False)
        self.declare_parameter("jpeg_quality", 80)
        self.declare_parameter("mock", False)
        self.declare_parameter("mock_source", "")
        # Track names are schema-confirmed; parameters only so a
        # schema change does not need a code change.
        self.declare_parameter("source_track", "source")
        self.declare_parameter("output_track", "main_video")

        g = self.get_parameter
        self.model_name = g("model_name").value
        self.input_topic = g("input_image_topic").value
        self.input_compressed = bool(g("input_compressed").value)
        self.initial_prompt = g("initial_prompt").value
        self.keep_backlog = bool(g("keep_backlog").value)
        self.publish_fps = float(g("publish_fps").value)
        self.frame_id = g("frame_id").value
        self.publish_compressed = bool(g("publish_compressed").value)
        self.jpeg_quality = int(g("jpeg_quality").value)
        self.mock = bool(g("mock").value)
        self.mock_source = g("mock_source").value
        self.source_track_name = g("source_track").value
        self.output_track_name = g("output_track").value

        self.lock = threading.Lock()

        self.source_frame = None      # newest Unity frame, for the source track
        self.out_frames = deque(maxlen=2)   # newest X2 output, for ROS
        self.pending_prompt = None
        self.session = {}
        self.loop = None

        self._pushed = 0              # frames handed to X2
        self._received = 0            # frames handed back by X2
        self._got_unity = False
        self._logged_first_output = False

        # RELIABLE out, BEST_EFFORT in -- see the QoS note in imgcodec.py. A
        # best-effort image publisher is invisible to rosbridge_server and
        # web_video_server, which is a blank panel with nothing in any log.
        pub_qos = image_pub_qos()
        sub_qos = image_sub_qos()
        self.pub_image = self.create_publisher(
            Image, "/world_model/image_raw", pub_qos)
        self.pub_compressed = self.create_publisher(
            CompressedImage, "/world_model/image_raw/compressed", pub_qos) \
            if self.publish_compressed else None
        self.pub_info = self.create_publisher(
            CameraInfo, "/world_model/camera_info", pub_qos)
        self.pub_state = self.create_publisher(
            String, "/world_model/state", 10)

        if self.input_compressed:
            source = self.input_topic + "/compressed"
            self.create_subscription(
                CompressedImage, source, self._on_unity_compressed, sub_qos)
            self.get_logger().info(f"source: {source} (CompressedImage)")
            source_type = "sensor_msgs/msg/CompressedImage"
        else:
            source = self.input_topic
            self.create_subscription(
                Image, source, self._on_unity_frame, sub_qos)
            self.get_logger().info(f"source: {source} (raw Image)")
            source_type = "sensor_msgs/msg/Image"
        # Without this the bridge pushes grey at X2 forever and says nothing
        # useful about why. See topicwatch.py.
        self.watch = TopicWatchdog(self, source, source_type, "camera")
        self.create_subscription(
            String, "/world_model/prompt", self._on_prompt, 10)

        self.create_timer(1.0 / self.publish_fps, self._publish_latest)
        self.create_timer(5.0, self._report)

        self._thread = threading.Thread(target=self._run_async, daemon=True)
        self._thread.start()

    # ---------------- ROS side ----------------

    def _on_unity_compressed(self, msg: CompressedImage):
        try:
            rgb = compressed_to_rgb(msg)
        except Exception as exc:  # noqa: BLE001
            self.get_logger().warn(f"bad compressed input frame: {exc}")
            return
        self._accept(rgb)

    def _on_unity_frame(self, msg: Image):
        try:
            rgb = msg_to_rgb(msg)
        except Exception as exc:  # noqa: BLE001
            self.get_logger().warn(f"bad input frame: {exc}")
            return
        self._accept(rgb)

    def _accept(self, rgb):
        self.watch.mark()
        with self.lock:
            self.source_frame = np.ascontiguousarray(rgb)
            if not self._got_unity:
                self._got_unity = True
                self.get_logger().info(
                    f"first Unity frame: {rgb.shape[1]}x{rgb.shape[0]}")

    def _on_prompt(self, msg: String):
        text = msg.data.strip()
        if not text:
            return
        if len(text) > 1000:
            self.get_logger().warn(
                f"prompt is {len(text)} chars; X2 caps at 1000. Truncating.")
            text = text[:1000]
        with self.lock:
            self.pending_prompt = text
        self.get_logger().info(f"prompt queued: {text[:60]}...")

    def _publish_latest(self):
        with self.lock:
            frame = self.out_frames[-1] if self.out_frames else None
        if frame is None:
            return

        stamp = self.get_clock().now().to_msg()

        msg = fill_msg(Image(), frame, "rgb8")
        msg.header.stamp = stamp
        msg.header.frame_id = self.frame_id
        self.pub_image.publish(msg)

        if self.pub_compressed is not None:
            bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
            ok, buf = cv2.imencode(
                ".jpg", bgr,
                [int(cv2.IMWRITE_JPEG_QUALITY), self.jpeg_quality])
            if ok:
                c = CompressedImage()
                c.header.stamp = stamp
                c.header.frame_id = self.frame_id
                c.format = "jpeg"
                c.data = buf.tobytes()
                self.pub_compressed.publish(c)

        # Synthetic intrinsics so downstream nodes that demand CameraInfo do
        # not fall over. There is no real camera -- treat any metric result
        # derived from these as fiction. Real depth comes from Unity.
        h, w = frame.shape[:2]
        f = float(w) * 0.8
        info = CameraInfo()
        info.header.stamp = stamp
        info.header.frame_id = self.frame_id
        info.height, info.width = h, w
        info.distortion_model = "plumb_bob"
        info.d = [0.0] * 5
        info.k = [f, 0.0, w / 2.0, 0.0, f, h / 2.0, 0.0, 0.0, 1.0]
        info.r = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
        info.p = [f, 0.0, w / 2.0, 0.0, 0.0, f, h / 2.0, 0.0, 0.0, 0.0, 1.0, 0.0]
        self.pub_info.publish(info)

    def _report(self):
        """Both counters, every 5s. A live session bills per second, so 'is
        anything actually flowing' has to be answerable at a glance."""
        if self.mock:
            return
        if self._pushed and not self._received:
            self.get_logger().warn(
                f"pushed {self._pushed} frames, received 0 back -- X2 needs a "
                f"non-empty prompt AND source frames before it generates")
        elif self._received:
            self.get_logger().info(
                f"source {self._pushed} frames / output {self._received} frames")

    def _emit_state(self, extra=None):
        payload = dict(self.session)
        if extra:
            payload.update(extra)
        msg = String()
        msg.data = json.dumps(payload)
        self.pub_state.publish(msg)

    # ---------------- Reactor side ----------------

    def _run_async(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            if self.mock or not REACTOR_AVAILABLE:
                self.loop.run_until_complete(self._mock_main())
            else:
                self.loop.run_until_complete(self._x2_main())
        except Exception as exc:  # noqa: BLE001
            self.get_logger().error(f"reactor loop died: {exc}")

    async def _x2_main(self):
        key = os.environ.get("REACTOR_API_KEY")
        if not key:
            self.get_logger().error("REACTOR_API_KEY is not set")
            return

        reactor = Reactor(model_name=self.model_name, api_key=key)

        @reactor.on_message
        def on_message(msg):
            kind = msg.get("type")
            data = msg.get("data", {})
            if kind == "state_update":
                self.session = data
                self._emit_state()
            elif kind == "generation_started":
                self.get_logger().info(
                    f"generating at {data.get('width')}x{data.get('height')}")
                self._emit_state({"event": "generation_started"})
            elif kind == "generation_stopped":
                reason = data.get("reason")
                # reference_image_changed is an automatic restart, not an end,
                # so it is not worth a warning. Routed through emit() because
                # binding one call site to two severities raises inside rclpy --
                # and this handler only runs during a live paid session, which
                # is the worst possible place to find that out. See roslog.py.
                emit(self.get_logger(),
                     "info" if reason == "reset" else "warn",
                     f"generation stopped: {reason}")
                self._emit_state({"event": "generation_stopped",
                                  "reason": reason})
            elif kind == "prompt_accepted":
                self.get_logger().info("prompt accepted")
            elif kind == "command_error":
                self.get_logger().error(
                    f"{data.get('command')} rejected: {data.get('reason')}")

        @reactor.on_error
        def on_error(err):
            self.get_logger().error(f"reactor error: {err}")

        # Outbound: the edited stream. Bound BY NAME and BEFORE connect, because
        # `reactor.tracks` stays empty until the model's capabilities arrive
        # shortly after connect() -- so `.with_direction(...).one()` here is a
        # race that raises "no track matches" on a fast connect. Registering on
        # the named track is explicitly supported ahead of time; the SDK adapts
        # the handler once the declaration lands.
        out = reactor.track(self.output_track_name)

        @out.on_frame
        def on_frame(frame):
            # Contiguous copy: the SDK hands back a reversed-stride view onto a
            # read-only buffer, and this frame outlives the callback.
            with self.lock:
                self.out_frames.append(np.ascontiguousarray(frame))
            self._received += 1
            if not self._logged_first_output:
                self._logged_first_output = True
                self.get_logger().info(
                    f"first X2 frame back: {frame.shape[1]}x{frame.shape[0]}")

        await reactor.connect()
        self.get_logger().info(f"connected to {self.model_name}")

        # Inbound: Unity's stream. This is what makes X2 an editor rather
        # than a generator, and why structure is preserved every frame.
        src = await reactor.publish_track(self.source_track_name)
        self.get_logger().info(
            f"publishing Unity frames on `{self.source_track_name}`")
        pump = asyncio.create_task(self._pump_source(src))

        # Frame policy. true = every frame in order (scoring runs);
        # false = newest only, bounded latency (live demo).
        await reactor.send_command(
            "set_keep_backlog", {"keep_backlog": self.keep_backlog})

        # There is NO start command. Generation begins by itself once a
        # non-empty prompt is set and source frames are flowing.
        if self.initial_prompt:
            await reactor.send_command(
                "set_prompt", {"prompt": self.initial_prompt[:1000]})
        else:
            self.get_logger().warn(
                "no initial_prompt -- X2 will not generate until "
                "something publishes on /world_model/prompt")

        try:
            while True:
                with self.lock:
                    prompt = self.pending_prompt
                    self.pending_prompt = None
                if prompt:
                    # Applies from the next block. Always a complete replacement:
                    # X2 has no relative follow-ups like "make it dustier".
                    await reactor.send_command("set_prompt", {"prompt": prompt})
                await asyncio.sleep(0.2)
        finally:
            pump.cancel()
            # Credits bill per second, so a dying node closes its session rather
            # than leaving it open for the socket to time out.
            try:
                await reactor.disconnect()
            except Exception as exc:  # noqa: BLE001
                self.get_logger().warn(f"disconnect failed: {exc}")

    async def _pump_source(self, src):
        """Hand Unity's newest frame to X2 at a steady 24 fps.

        push_frame() is synchronous and does no pacing of its own, so the clock
        lives here. X2 paces its source at 24 fps: if Unity renders slower we
        repeat the last frame, if faster the newest wins. Either way frames must
        keep arriving -- generation stalls without them.
        """
        period = 1.0 / SOURCE_FPS
        placeholder = np.full((768, 1280, 3), 96, dtype=np.uint8)
        next_t = time.monotonic()
        warned_empty = False

        while True:
            next_t += period
            with self.lock:
                frame = self.source_frame
            if frame is None:
                frame = placeholder
                if not warned_empty and self._pushed > SOURCE_FPS * 5:
                    warned_empty = True
                    self.get_logger().warn(
                        f"5s of grey placeholder at X2 -- no frame has ever "
                        f"arrived on {self.input_topic}. This is a paid "
                        f"session repainting a blank rectangle; fix the source "
                        f"before spending more. See the `camera:` warnings "
                        f"above, or run `./rig topics`.")
            try:
                src.push_frame(frame)
                self._pushed += 1
            except Exception as exc:  # noqa: BLE001
                self.get_logger().error(f"push_frame failed: {exc}")
                await asyncio.sleep(1.0)
                next_t = time.monotonic()
                continue

            delay = next_t - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            else:
                # Fell behind. Resync rather than sprinting to catch up, which
                # would push a burst of stale frames at the model.
                next_t = time.monotonic()

    # ---------------- mock ----------------

    async def _mock_main(self):
        """Echo the Unity frame with a tint, or replay mock_source.

        No API, no credits, no session conflict. Same topics, so everything
        downstream can be built and tested against this.
        """
        self.get_logger().warn("MOCK MODE -- output is not from X2")
        cap = None
        if self.mock_source and os.path.isfile(self.mock_source):
            cap = cv2.VideoCapture(self.mock_source)

        n = 0
        while True:
            frame = None
            if cap is not None:
                ok, bgr = cap.read()
                if not ok:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    ok, bgr = cap.read()
                if ok:
                    frame = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            if frame is None:
                with self.lock:
                    src = self.source_frame
                if src is not None:
                    # Crude "Mars" tint so it is obviously mock.
                    frame = src.astype(np.float32)
                    frame[:, :, 0] *= 1.35
                    frame[:, :, 2] *= 0.65
                    frame = np.clip(frame, 0, 255).astype(np.uint8)
                else:
                    frame = np.full((768, 1280, 3), 110, dtype=np.uint8)
                    frame[:, :, 0] = 170

            cv2.putText(frame, f"MOCK {n}", (24, 48),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)
            with self.lock:
                self.out_frames.append(frame)

            self.session = {"generating": True, "mock": True,
                            "prompt": None, "width": frame.shape[1],
                            "height": frame.shape[0]}
            self._emit_state()
            n += 1
            await asyncio.sleep(1.0 / self.publish_fps)


def main():
    rclpy.init()
    node = X2Bridge()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        # ros2 launch sends SIGINT, rclpy's signal handler shuts the context
        # down, and spin() then raises. Calling shutdown() again from here
        # raises RCLError("rcl_shutdown already called") from inside the
        # finally block, so the node exits 1 and launch prints
        # "process has died" for a perfectly normal Ctrl-C. Real crashes then
        # look exactly like a clean exit, which is the actual cost.
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
