#!/usr/bin/env python3
"""perception_node.py: runs segmentation on the world model's output.

    /world_model/image_raw  ->  [ segmentation ]  ->  /perception/mask

Never subscribes to /unity/labels: perception sees only camera input and the
scorer alone holds the ground truth. `method` defaults to the classical
baseline in perception.py; add a branch to `segment()` for another model.

    ros2 run mars_rig perception_node
    ros2 run mars_rig perception_node --ros-args \
        -p input_topic:=/unity/camera/image_raw     # the ceiling measurement
"""

import cv2
import numpy as np
import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from sensor_msgs.msg import CompressedImage, Image

import json

from mars_rig.imgcodec import (
    compressed_to_rgb, fill_msg, image_pub_qos, image_sub_qos, msg_to_rgb,
)
from mars_rig.labels import DEFAULT_PALETTE, DEFAULT_SCORED, LabelSpace
from mars_rig.perception import colourise, segment
from mars_rig.topicwatch import TopicWatchdog


class PerceptionNode(Node):
    def __init__(self):
        super().__init__("perception_node")

        self.declare_parameter("input_topic", "/world_model/image_raw")
        # Raw by default: both upstream publishers (x2_bridge, Unity) publish raw.
        self.declare_parameter("use_compressed", False)
        self.declare_parameter("output_topic", "/perception/mask")
        self.declare_parameter("method", "baseline_cv")
        self.declare_parameter("output_encoding", "rgb8")
        # Optional PNG copy (PNG, as JPEG bleeds class colours). Off by default:
        # the panel and web_video_server read the raw mask topic.
        self.declare_parameter("publish_compressed", False)
        self.declare_parameter("palette_json", "")
        self.declare_parameter("scored_classes", list(DEFAULT_SCORED))
        self.declare_parameter("max_rate_hz", 12.0)
        # baseline_cv tuning knobs.
        self.declare_parameter("horizon_frac", 0.25)
        self.declare_parameter("texture_thresh", 6.0)
        self.declare_parameter("shadow_offset", 22.0)
        self.declare_parameter("min_blob_px", 24)

        g = self.get_parameter
        self.method = g("method").value
        self.output_encoding = g("output_encoding").value
        self.max_rate = float(g("max_rate_hz").value)
        self.kw = {
            "horizon_frac": float(g("horizon_frac").value),
            "texture_thresh": float(g("texture_thresh").value),
            "shadow_offset": float(g("shadow_offset").value),
            "min_blob_px": int(g("min_blob_px").value),
        }

        palette = DEFAULT_PALETTE
        raw = g("palette_json").value
        if raw:
            palette = {k: tuple(v) for k, v in json.loads(raw).items()}
        self.space = LabelSpace(palette=palette,
                                scored=list(g("scored_classes").value))

        self._last = 0.0
        self._frames = 0

        # RELIABLE out, BEST_EFFORT in (see the QoS note in imgcodec.py).
        pub_qos = image_pub_qos()
        sub_qos = image_sub_qos()
        out_topic = g("output_topic").value
        self.pub = self.create_publisher(Image, out_topic, pub_qos)
        self.publish_compressed = bool(g("publish_compressed").value)
        self.pub_compressed = self.create_publisher(
            CompressedImage, out_topic + "/compressed", pub_qos) \
            if self.publish_compressed else None

        topic = g("input_topic").value
        if bool(g("use_compressed").value):
            # image_transport convention: compressed frames are on <topic>/compressed.
            topic = topic + "/compressed"
            self.create_subscription(
                CompressedImage, topic, self._on_compressed, sub_qos)
            topic_type = "sensor_msgs/msg/CompressedImage"
        else:
            self.create_subscription(Image, topic, self._on_image, sub_qos)
            topic_type = "sensor_msgs/msg/Image"
        # Warns if the input topic goes quiet (see topicwatch.py).
        self.watch = TopicWatchdog(self, topic, topic_type, "input")

        self.get_logger().info(
            f"perception up: method={self.method} in={topic} "
            f"out={g('output_topic').value} classes={self.space.names}")
        if self.method == "baseline_cv":
            self.get_logger().warn(
                "baseline_cv is the classical stand-in (model choice is "
                "still open) -- results are a baseline, not the MRT stack")

    def _rate_limited(self) -> bool:
        now = self.get_clock().now().nanoseconds / 1e9
        if self.max_rate > 0 and (now - self._last) < (1.0 / self.max_rate):
            return True
        self._last = now
        return False

    def _on_compressed(self, msg: CompressedImage):
        self.watch.mark()
        if self._rate_limited():
            return
        try:
            rgb = compressed_to_rgb(msg)
        except Exception as exc:  # noqa: BLE001
            self.get_logger().warn(f"undecodable compressed frame: {exc}")
            return
        self._run(np.asarray(rgb), msg.header)

    def _on_image(self, msg: Image):
        self.watch.mark()
        if self._rate_limited():
            return
        try:
            rgb = msg_to_rgb(msg)
        except Exception as exc:  # noqa: BLE001
            self.get_logger().warn(f"undecodable frame: {exc}")
            return
        self._run(np.asarray(rgb), msg.header)

    def _run(self, rgb: np.ndarray, header):
        try:
            ids = segment(rgb, self.space, self.method, **self.kw)
        except Exception as exc:  # noqa: BLE001
            self.get_logger().error(f"segmentation failed: {exc}")
            return

        if self.output_encoding == "mono8":
            out = fill_msg(Image(), ids, "mono8")
        else:
            out = fill_msg(Image(), colourise(ids, self.space), "rgb8")

        # Keep the source stamp so the scorer can pair this with its label frame.
        out.header = header
        self.pub.publish(out)

        if self.pub_compressed is not None:
            colour = colourise(ids, self.space)
            ok, buf = cv2.imencode(".png", colour[:, :, ::-1])
            if ok:
                c = CompressedImage()
                c.header = header
                c.format = "png"
                c.data = buf.tobytes()
                self.pub_compressed.publish(c)

        self._frames += 1


def main():
    rclpy.init()
    node = PerceptionNode()
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
