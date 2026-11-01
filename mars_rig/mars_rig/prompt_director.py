#!/usr/bin/env python3
"""prompt_director.py: the automated prompt loop (ROS wrapper).

Reads the score, picks the next condition, publishes the prompt, records results.

    /score/iou           Float32   score from the scoring node
    /score/detail        String    per-class IoU JSON
         ->
    /world_model/prompt  String    prompt text for the bridge to send to X2
    /director/status     String    JSON, for the control panel UI

Modes:
  sweep     walk every prompt in the escalation ladder; writes results.csv/md
  adaptive  escalate while the score stays high, stop when the stack breaks
  llm       adaptive, with a local Ollama model proposing conditions; falls
            back to the catalog on error, timeout or banned word

    ros2 run mars_rig prompt_director --ros-args \
        -p mode:=sweep -p catalog:=/path/prompt_catalog_x2.json

Each condition needs a settle period (discarded) before the dwell (measured),
as X2 applies prompts at chunk boundaries. The decision logic lives in
director_core.py; this node is only the ROS wiring.
"""

import json

import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from std_msgs.msg import Float32, String

from mars_rig.director_core import Catalog, DirectorCore, OllamaProposer
from mars_rig.roslog import emit


class PromptDirector(Node):
    def __init__(self):
        super().__init__("prompt_director")

        self.declare_parameter("mode", "sweep")
        self.declare_parameter("catalog", "prompt_catalog_x2.json")
        self.declare_parameter("lang", "zh")
        self.declare_parameter("settle_sec", 8.0)
        self.declare_parameter("dwell_sec", 25.0)
        self.declare_parameter("out_dir", "results")
        self.declare_parameter("escalate_above", 0.75)
        # Mean IoU over {drivable, obstacle} stays near 0.45 or above while
        # drivable is easy, so a lower threshold would never fire.
        self.declare_parameter("break_below", 0.50)
        self.declare_parameter("ollama_url", "http://localhost:11434/api/chat")
        self.declare_parameter("ollama_model", "qwen2.5:3b-instruct")

        g = self.get_parameter
        mode = g("mode").value

        try:
            catalog = Catalog(g("catalog").value)
        except FileNotFoundError as exc:
            raise SystemExit(str(exc)) from exc

        llm = None
        if mode == "llm":
            llm = OllamaProposer(
                catalog, g("ollama_url").value, g("ollama_model").value,
                log=self._log)

        self.core = DirectorCore(
            catalog,
            mode=mode,
            lang=g("lang").value,
            settle_sec=float(g("settle_sec").value),
            dwell_sec=float(g("dwell_sec").value),
            out_dir=g("out_dir").value,
            escalate_above=float(g("escalate_above").value),
            break_below=float(g("break_below").value),
            emit=self._emit_prompt,
            log=self._log,
            llm=llm,
        )

        self.pub_prompt = self.create_publisher(
            String, "/world_model/prompt", 10)
        self.pub_status = self.create_publisher(
            String, "/director/status", 10)
        self.create_subscription(Float32, "/score/iou", self._on_score, 10)
        # Reporting only; escalation uses /score/iou.
        self.create_subscription(String, "/score/detail", self._on_detail, 10)

        self.create_timer(0.5, self._tick)
        self.get_logger().info(
            f"director up: mode={self.core.mode} lang={self.core.lang} "
            f"settle={self.core.settle_sec}s dwell={self.core.dwell_sec}s "
            f"ladder={len(self.core.ladder)} conditions")

    # ---------- core callbacks ----------

    def _emit_prompt(self, text, prompt):
        """Forwarded verbatim."""
        self.pub_prompt.publish(String(data=text))

    def _log(self, level, msg):
        # Via emit(): rclpy rejects varying severities from one call site.
        emit(self.get_logger(), level, msg)

    # ---------- ros ----------

    def now(self):
        return self.get_clock().now().nanoseconds / 1e9

    def _on_score(self, msg: Float32):
        self.core.add_score(msg.data)

    def _on_detail(self, msg: String):
        self.core.add_detail(msg.data)

    def _tick(self):
        now = self.now()
        self.core.tick(now)
        self.pub_status.publish(
            String(data=json.dumps(self.core.status(now))))


def main():
    rclpy.init()
    node = PromptDirector()
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
