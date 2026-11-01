#!/usr/bin/env python3
"""
scoring_node.py -- IoU(prediction, Unity ground truth).

The only node in the graph that is allowed to see the label camera. Perception
never subscribes to it; if it did, the whole experiment would be measuring
nothing.

TOPICS
------
Subscribes:
    /perception/mask     sensor_msgs/Image    prediction
    /unity/labels        sensor_msgs/Image    ground truth (answer key)
Publishes:
    /score/iou           std_msgs/Float32     mean IoU -- this drives the director
    /score/detail        std_msgs/String      JSON: per-class IoU, pixel counts

RUN
---
    ros2 run mars_rig scoring_node

    # director testing with no perception and no Unity (section 8, test 4):
    ros2 run mars_rig scoring_node --ros-args -p fake_ramp:=true

SANITY CHECK BEFORE TRUSTING ANY NUMBER
---------------------------------------
Score the plain Unity render against its own labels. That is the CEILING. If it
is low, perception or label alignment is broken and no world-model result means
anything.
"""

import json
import time

import numpy as np
import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from sensor_msgs.msg import CompressedImage, Image
from std_msgs.msg import Float32, String

import message_filters

from mars_rig.imgcodec import (
    compressed_to_rgb, image_sub_qos, msg_to_rgb, msg_to_single_channel,
)
from mars_rig.labels import DEFAULT_PALETTE, DEFAULT_SCORED, LabelSpace, score
from mars_rig.topicwatch import TopicWatchdog

try:
    import cv2
    CV2_AVAILABLE = True
except ImportError:  # pragma: no cover - cv2 ships with the ROS image stack
    CV2_AVAILABLE = False


class ScoringNode(Node):
    def __init__(self):
        super().__init__("scoring_node")

        self.declare_parameter("prediction_topic", "/perception/mask")
        self.declare_parameter("ground_truth_topic", "/unity/labels")
        # Unity can publish labels as PNG CompressedImage instead of raw.
        # PNG is lossless so classes survive exactly, and a flat-colour
        # label image compresses enormously -- worth it if the raw topic is
        # crowding the link. JPEG is never an option here: it bleeds colour
        # across class boundaries and nothing downstream can undo it.
        self.declare_parameter("ground_truth_compressed", False)
        self.declare_parameter("max_rate_hz", 10.0)
        self.declare_parameter("sync_slop_sec", 0.15)
        self.declare_parameter("sync_queue_size", 30)
        # JSON so the whole palette can be swapped from a launch file once
        # Unity's replacement shader colours are pinned down.
        self.declare_parameter("palette_json", "")
        self.declare_parameter("scored_classes", list(DEFAULT_SCORED))
        self.declare_parameter("colour_max_distance", 96.0)
        self.declare_parameter("fake_ramp", False)
        self.declare_parameter("fake_ramp_start", 0.85)
        self.declare_parameter("fake_ramp_end", 0.05)
        self.declare_parameter("fake_ramp_sec", 180.0)

        g = self.get_parameter
        self.max_rate = float(g("max_rate_hz").value)
        self.fake_ramp = bool(g("fake_ramp").value)

        palette = DEFAULT_PALETTE
        raw = g("palette_json").value
        if raw:
            palette = {k: tuple(v) for k, v in json.loads(raw).items()}
        self.space = LabelSpace(
            palette=palette,
            scored=list(g("scored_classes").value),
            max_distance=float(g("colour_max_distance").value),
        )

        self._last_pub = 0.0
        self._warned_resize = False
        self._warned_shape = False
        self._pairs = 0

        self.pub_iou = self.create_publisher(Float32, "/score/iou", 10)
        self.pub_detail = self.create_publisher(String, "/score/detail", 10)

        if self.fake_ramp:
            self._ramp_start = float(g("fake_ramp_start").value)
            self._ramp_end = float(g("fake_ramp_end").value)
            self._ramp_sec = float(g("fake_ramp_sec").value)
            self._t0 = time.monotonic()
            self.create_timer(1.0 / max(self.max_rate, 0.1), self._tick_fake)
            self.get_logger().warn(
                f"FAKE RAMP -- publishing a synthetic descending score "
                f"{self._ramp_start} -> {self._ramp_end} over {self._ramp_sec}s. "
                f"Nothing here is measured.")
            return

        # BEST_EFFORT in -- accepts frames from any publisher, reliable or
        # not. See the QoS note in imgcodec.py.
        qos = image_sub_qos(depth=5)
        sub_pred = message_filters.Subscriber(
            self, Image, g("prediction_topic").value, qos_profile=qos)
        gt_topic = g("ground_truth_topic").value
        if bool(g("ground_truth_compressed").value):
            gt_topic = gt_topic + "/compressed"
            sub_gt = message_filters.Subscriber(
                self, CompressedImage, gt_topic, qos_profile=qos)
        else:
            sub_gt = message_filters.Subscriber(
                self, Image, gt_topic, qos_profile=qos)

        # Unsynced comparison silently produces garbage: the prediction for one
        # frame scored against another frame's labels still yields a plausible
        # number, and nothing anywhere reports an error.
        self._sync = message_filters.ApproximateTimeSynchronizer(
            [sub_pred, sub_gt],
            int(g("sync_queue_size").value),
            float(g("sync_slop_sec").value),
        )
        self._sync.registerCallback(self._on_pair)

        # Count each side separately. "No score" has three different causes and
        # they need three different fixes: no prediction, no ground truth, or
        # both arriving but never pairing. Guessing between them from an empty
        # /score/iou is exactly the hour this rig cannot afford.
        pred_type = "sensor_msgs/msg/Image"
        gt_type = ("sensor_msgs/msg/CompressedImage"
                   if bool(g("ground_truth_compressed").value) else pred_type)
        self.watch_pred = TopicWatchdog(
            self, g("prediction_topic").value, pred_type, "prediction")
        self.watch_gt = TopicWatchdog(self, gt_topic, gt_type, "ground truth")
        self._pred_topic = g("prediction_topic").value
        self._gt_topic = gt_topic
        sub_pred.registerCallback(lambda _m: self.watch_pred.mark())
        sub_gt.registerCallback(lambda _m: self.watch_gt.mark())
        self._sync_period = float(g("sync_slop_sec").value)
        self.create_timer(10.0, self._report_health)

        self.get_logger().info(
            f"scoring {g('prediction_topic').value} against "
            f"{gt_topic}; scored classes "
            f"{self.space.scored_names}; max {self.max_rate} Hz")

    def _report_health(self):
        """Say why there is no score, in the log AND on /score/detail.

        An empty score panel is ambiguous: it looks the same whether scoring is
        broken, starting up, or structurally impossible because there is no
        ground-truth topic in this graph at all. Publishing the reason means
        the panel and the director can state it instead of showing a blank
        number, and nothing downstream has to guess.

        No /score/iou is published here on purpose. A placeholder score is a
        number someone will eventually quote.
        """
        if self._pairs:
            return

        pred, gt = self.watch_pred.count, self.watch_gt.count
        if not pred and not gt:
            reason = (f"no frames on either {self._pred_topic} or "
                      f"{self._gt_topic}")
        elif not gt:
            reason = (f"no ground truth: nothing on {self._gt_topic}. IoU "
                      f"needs a semantic-label image as well as the "
                      f"prediction, so no score is possible until something "
                      f"publishes it.")
        elif not pred:
            reason = f"no prediction: nothing on {self._pred_topic}"
        else:
            # ApproximateTimeSynchronizer matches on HEADER STAMPS, not arrival
            # time. Two publishers that never share a clock -- one stamping
            # with now(), one republishing a source stamp, one leaving it at
            # zero -- produce a healthy stream on both topics and not one pair,
            # and the synchroniser reports nothing at all.
            reason = (f"{pred} predictions and {gt} ground truth frames, but 0 "
                      f"pairs. ApproximateTimeSynchronizer matches header "
                      f"stamps, not arrival time: check both topics carry a "
                      f"real stamp from the SAME clock (`ros2 topic echo "
                      f"--field header.stamp <topic>`). Widen with "
                      f"-p sync_slop_sec:= (currently {self._sync_period}) "
                      f"only once the stamps are known to be sane.")
            self.get_logger().warn(reason)

        self.pub_detail.publish(String(data=json.dumps({
            "unavailable": True, "reason": reason,
        })))

    # ---------- fake ramp ----------

    def _tick_fake(self):
        """A descending score with no inputs, for testing the director alone."""
        frac = min(1.0, (time.monotonic() - self._t0) / max(self._ramp_sec, 1e-6))
        value = self._ramp_start + (self._ramp_end - self._ramp_start) * frac
        self.pub_iou.publish(Float32(data=float(value)))
        self.pub_detail.publish(String(data=json.dumps({
            "fake_ramp": True, "mean_iou": round(value, 5), "progress": round(frac, 3),
        })))

    # ---------- real scoring ----------

    def _to_array(self, msg, what: str):
        """Image or CompressedImage -> class ids, whatever encoding arrives."""
        try:
            if isinstance(msg, CompressedImage):
                if "jpeg" in (msg.format or "").lower() and what == "ground truth":
                    # Loud, because the resulting IoU looks entirely plausible.
                    self.get_logger().error(
                        "ground truth arrived as JPEG. Lossy compression bleeds "
                        "colour across class boundaries -- republish the label "
                        "camera as PNG or raw. Scores from this run are void.")
                img = compressed_to_rgb(msg)
            elif msg.encoding in ("mono8", "8UC1"):
                img = msg_to_single_channel(msg)
            else:
                img = msg_to_rgb(msg)
        except Exception as exc:  # noqa: BLE001
            self.get_logger().warn(f"undecodable {what} frame: {exc}")
            return None
        return self.space.quantise(np.asarray(img))

    def _on_pair(self, pred_msg: Image, gt_msg: Image):
        now = time.monotonic()
        # The director averages these; it does not need every frame, and at 24 fps
        # the quantisation below is the most expensive thing in the graph.
        if self.max_rate > 0 and (now - self._last_pub) < (1.0 / self.max_rate):
            return

        pred = self._to_array(pred_msg, "prediction")
        gt = self._to_array(gt_msg, "ground truth")
        if pred is None or gt is None:
            return

        if pred.shape != gt.shape:
            # X2 picks its own output resolution (~832p bucket) which will not
            # match Unity's render size, so perception's mask inherits that size.
            if not self._warned_resize:
                self.get_logger().warn(
                    f"resizing prediction {pred.shape[1]}x{pred.shape[0]} -> "
                    f"ground truth {gt.shape[1]}x{gt.shape[0]}; every IoU below "
                    f"includes this resample")
                self._warned_resize = True
            pred = self._resize_ids(pred, gt.shape)
            if pred is None:
                return

        mean, detail = score(pred, gt, self.space)
        if mean is None:
            if not self._warned_shape:
                self.get_logger().warn(
                    "no scored class present in either image -- check the "
                    "palette matches Unity's label shader colours")
                self._warned_shape = True
            return

        self._last_pub = now
        self._pairs += 1
        detail["pairs"] = self._pairs
        self.pub_iou.publish(Float32(data=float(mean)))
        self.pub_detail.publish(String(data=json.dumps(detail)))

    def _resize_ids(self, ids, shape):
        """Nearest-neighbour only. Interpolating class ids invents classes."""
        if not CV2_AVAILABLE:
            self.get_logger().error("cv2 missing -- cannot resize prediction")
            return None
        return cv2.resize(
            ids, (shape[1], shape[0]), interpolation=cv2.INTER_NEAREST)


def main():
    rclpy.init()
    node = ScoringNode()
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
