#!/usr/bin/env python3
"""fake_score_publisher.py: synthetic scores for exercising the director.

Lets results.csv and results.md be checked with no Unity, X2, perception or
API credits.

    ros2 run mars_rig fake_score_publisher --ros-args -p mode:=ramp
    ros2 run mars_rig prompt_director --ros-args \
        -p settle_sec:=2.0 -p dwell_sec:=3.0

Modes:
  ramp      linear descent from `start` to `end` over `duration_sec`
  steps     holds a level, dropping each time the prompt changes
  constant  a fixed value

Publishes on /score/iou like scoring_node; never run both.
"""

import json
import math
import random
import time

import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from std_msgs.msg import Float32, String


class FakeScorePublisher(Node):
    def __init__(self):
        super().__init__("fake_score_publisher")

        self.declare_parameter("mode", "steps")
        self.declare_parameter("rate_hz", 10.0)
        self.declare_parameter("start", 0.86)
        self.declare_parameter("end", 0.05)
        self.declare_parameter("duration_sec", 300.0)
        self.declare_parameter("constant", 0.5)
        self.declare_parameter("noise", 0.02)
        self.declare_parameter("step_decay", 0.88)
        self.declare_parameter("seed", 0)

        g = self.get_parameter
        self.mode = g("mode").value
        self.rate = float(g("rate_hz").value)
        self.start = float(g("start").value)
        self.end = float(g("end").value)
        self.duration = float(g("duration_sec").value)
        self.constant = float(g("constant").value)
        self.noise = float(g("noise").value)
        self.step_decay = float(g("step_decay").value)

        # Seeded for reproducible runs.
        self.rng = random.Random(int(g("seed").value))

        self.level = self.start
        self.t0 = time.monotonic()
        self._seen_prompt = None

        self.pub = self.create_publisher(Float32, "/score/iou", 10)
        self.pub_detail = self.create_publisher(String, "/score/detail", 10)
        self.create_subscription(
            String, "/world_model/prompt", self._on_prompt, 10)
        self.create_timer(1.0 / max(self.rate, 0.1), self._tick)

        self.get_logger().warn(
            f"FAKE SCORES on /score/iou (mode={self.mode}) -- nothing here is "
            f"measured. Do not run alongside scoring_node.")

    def _on_prompt(self, msg: String):
        """Each new condition knocks the level down, as escalation would."""
        if msg.data == self._seen_prompt:
            return
        self._seen_prompt = msg.data
        if self.mode == "steps":
            self.level = max(self.end, self.level * self.step_decay)

    def _tick(self):
        elapsed = time.monotonic() - self.t0
        if self.mode == "ramp":
            frac = min(1.0, elapsed / max(self.duration, 1e-6))
            base = self.start + (self.end - self.start) * frac
        elif self.mode == "constant":
            base = self.constant
        else:
            base = self.level

        value = base + self.rng.gauss(0.0, self.noise)
        value = max(0.0, min(1.0, value))
        self.pub.publish(Float32(data=float(value)))
        self.pub_detail.publish(String(data=json.dumps({
            "fake": True, "mode": self.mode, "mean_iou": round(value, 5),
            "elapsed": round(elapsed, 1),
        })))


def main():
    rclpy.init()
    node = FakeScorePublisher()
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
