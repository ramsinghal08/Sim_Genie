#!/usr/bin/env python3
"""
prompt_director.py -- the automated prompt loop.

Reads the score, decides the next condition, publishes the prompt, records
the result. Three modes; sweep is the one that produces your deliverable.

    /score/iou           Float32   perception score from the scoring node
    /world_model/state   String    JSON from the bridge (chunk number etc.)
         ->
    /world_model/prompt  String    prompt text for the bridge to send to X2
    /director/status     String    JSON, for the control panel UI

MODES
-----
sweep      Walk every prompt in the escalation ladder, dwell on each, record
           mean IoU. Deterministic, unattended, produces results.csv and
           results.md. THIS IS WHAT GENERATES YOUR RESULTS TABLE.

adaptive   Escalate while the score stays high, stop when the stack breaks.
           This is the demo mode: it hunts for the failure point live.

llm        Adaptive, but a local Ollama model composes novel conditions from
           the catalog it has already tried. Falls back to the catalog on any
           error, timeout, or banned word.

RUN
---
    ros2 run mars_rig prompt_director --ros-args \
        -p mode:=sweep -p catalog:=/path/prompt_catalog_x2.json

    ros2 run mars_rig prompt_director --ros-args -p mode:=adaptive
    ros2 run mars_rig prompt_director --ros-args -p mode:=llm

TIMING
------
X2 applies prompts at generation-chunk boundaries, so every condition needs a
settle period before its scores mean anything. settle_sec is discarded,
dwell_sec is measured. Do not shorten settle_sec to save time -- you will be
scoring the previous condition.

WHERE THE LOGIC LIVES
---------------------
The phase machine, the escalation rules and the results writer are in
director_core.py, which has no rclpy in it, so the sweep can be tested and
dry-run without a graph (tools/offline_loop.py). This node is the ROS wiring
around it and nothing else.
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
        # 0.50, not 0.25: mean IoU over {drivable, obstacle} cannot fall much
        # below 0.45 while drivable stays trivially easy, so a 0.25 threshold
        # could never fire on the most likely failure -- perception going
        # obstacle-blind while still calling the ground ground. Adaptive would
        # have walked the whole ladder and reported "never broke".
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
        # Reporting only -- escalation still runs off the mean on /score/iou.
        self.create_subscription(String, "/score/detail", self._on_detail, 10)

        self.create_timer(0.5, self._tick)
        self.get_logger().info(
            f"director up: mode={self.core.mode} lang={self.core.lang} "
            f"settle={self.core.settle_sec}s dwell={self.core.dwell_sec}s "
            f"ladder={len(self.core.ladder)} conditions")

    # ---------- core callbacks ----------

    def _emit_prompt(self, text, prompt):
        """Forwarded verbatim. The bridge does not edit it either."""
        self.pub_prompt.publish(String(data=text))

    def _log(self, level, msg):
        # Via emit(), not getattr: rclpy keys its logging context on the call
        # site and refuses a severity change under it, so one dynamic line for
        # every severity raises on the first warning. See roslog.py.
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
