#!/usr/bin/env python3
"""x2_bridge_node.py: ROS 2 <-> Reactor X2 bridge.

X2 is a conditional video model: it edits an inbound video track rather than
generating from scratch, so structure comes from the source on every frame.

    Unity frames  --> [source track]  --> X2 --> [main_video] --> ROS 2

Subscribes:
    <input_image_topic>       sensor_msgs/Image    Unity frame, raw rgb8
                              (input_compressed:=true reads
                              <input_image_topic>/compressed instead)
    /world_model/prompt       std_msgs/String      from prompt_director.py

Publishes:
    /world_model/image_raw            sensor_msgs/Image
    /world_model/camera_info          sensor_msgs/CameraInfo
    /world_model/state                std_msgs/String   JSON session state
    /world_model/image_raw/compressed sensor_msgs/CompressedImage
                              only when publish_compressed:=true

    export REACTOR_API_KEY=rk_...
    ros2 run mars_rig x2_bridge_node --ros-args \
        -p input_image_topic:=/unity/camera/image_raw \
        -p keep_backlog:=true

    ros2 run mars_rig x2_bridge_node --ros-args -p mock:=true   # no API, no credits

keep_backlog: false drops backlog for bounded latency (live feed); true edits
every source frame in order (recorded clips, reproducible scoring runs).

Media API (reactor-sdk 1.1.1): the SDK owns the media path (no aiortc or av).

    src = await reactor.publish_track("source")   # a track name, not an object
    src.push_frame(rgb)                           # sync, (h, w, 3) uint8 RGB

push_frame does no pacing, so _pump_source() owns the 24 fps clock. Inbound,
`track.on_frame(fn)` receives an RGB (h, w, 3) array.
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

SOURCE_FPS = 24          # X2's native source pacing


class X2Bridge(Node):
    def __init__(self):
        super().__init__("x2_bridge")

        self.declare_parameter("model_name", "x2")
        self.declare_parameter("input_image_topic", "/unity/camera/image_raw")
        # Raw by default, matching Unity's publisher. Enable only alongside a
        # CompressedImage publisher (raw rgb8 at 832x512x24fps is ~30 MiB/s).
        self.declare_parameter("input_compressed", False)
        self.declare_parameter("initial_prompt", "")
        self.declare_parameter("keep_backlog", True)
        self.declare_parameter("publish_fps", 24.0)
        self.declare_parameter("frame_id", "x2_cam")
        # Compressed companion topic is opt-in (nothing in the rig reads it).
        self.declare_parameter("publish_compressed", False)
        self.declare_parameter("jpeg_quality", 80)
        self.declare_parameter("mock", False)
        self.declare_parameter("mock_source", "")
        # Track names come from the model schema; parameters allow overrides.
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

        # RELIABLE out, BEST_EFFORT in (see the QoS note in imgcodec.py).
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
        # Warns if the source topic is silent (see topicwatch.py).
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

        # Synthetic intrinsics for nodes that require CameraInfo; not a real
        # camera model.
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
        """Log frame counters every 5 s (live sessions are billed per second)."""
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
                # Only "reset" is expected (automatic restart); anything else
                # warns. Uses emit() because one call site cannot log two
                # severities in rclpy (see roslog.py).
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

        # Outbound edited stream, bound by name before connect: `reactor.tracks`
        # is empty until capabilities arrive, so a lookup would race.
        out = reactor.track(self.output_track_name)

        @out.on_frame
        def on_frame(frame):
            # Copy: the SDK's buffer is a read-only view that must not outlive
            # the callback.
            with self.lock:
                self.out_frames.append(np.ascontiguousarray(frame))
            self._received += 1
            if not self._logged_first_output:
                self._logged_first_output = True
                self.get_logger().info(
                    f"first X2 frame back: {frame.shape[1]}x{frame.shape[0]}")

        await reactor.connect()
        self.get_logger().info(f"connected to {self.model_name}")

        # Inbound: Unity's stream, which X2 edits.
        src = await reactor.publish_track(self.source_track_name)
        self.get_logger().info(
            f"publishing Unity frames on `{self.source_track_name}`")
        pump = asyncio.create_task(self._pump_source(src))

        # true = every frame in order; false = newest only.
        await reactor.send_command(
            "set_keep_backlog", {"keep_backlog": self.keep_backlog})

        # No start command: generation begins once a prompt is set and
        # source frames are flowing.
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
                    # Applies from the next chunk; always a full replacement.
                    await reactor.send_command("set_prompt", {"prompt": prompt})
                await asyncio.sleep(0.2)
        finally:
            pump.cancel()
            # Close the session promptly: credits bill per second.
            try:
                await reactor.disconnect()
            except Exception as exc:  # noqa: BLE001
                self.get_logger().warn(f"disconnect failed: {exc}")

    async def _pump_source(self, src):
        """Hand Unity's newest frame to X2 at a steady 24 fps.

        Repeats the last frame if Unity is slower; the newest wins if faster.
        Generation stalls without a steady stream.
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
                # Fell behind: resync rather than burst stale frames.
                next_t = time.monotonic()

    # ---------------- mock ----------------

    async def _mock_main(self):
        """Echo the Unity frame with a tint, or replay mock_source.

        Publishes the same topics as the real path, with no API use.
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
                    # Tint so mock output is recognisable.
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
        # rclpy's SIGINT handler already shut the context down; a second
        # shutdown() would raise RCLError.
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
