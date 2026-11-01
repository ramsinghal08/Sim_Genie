#!/usr/bin/env python3
"""topicwatch.py: warn when a subscription never receives anything.

A ROS 2 subscription to a topic nobody publishes is silent, which makes a wrong
topic name look like a slow camera. The watchdog distinguishes the causes:

    no publisher at all   -> wrong topic name, node not running, or a different
                             ROS_DOMAIN_ID; lists same-type topics that exist
    publishers, no data   -> QoS mismatch, or a stalled publisher
    data, but not paired  -> synchronised pairs only; see scoring_node

Usage:
    self.watch = TopicWatchdog(self, "/unity/camera/image_raw",
                               "sensor_msgs/msg/Image", "camera")
    ...
    def _on_frame(self, msg):
        self.watch.mark()
"""

from __future__ import annotations


class TopicWatchdog:
    """Warn, on a timer, while `topic` has produced no messages.

    Stops after the first message arrives, logging that once.
    """

    def __init__(self, node, topic: str, type_name: str, label: str,
                 period: float = 8.0):
        self.node = node
        self.topic = topic
        self.type_name = type_name
        self.label = label
        self.count = 0
        self._announced = False
        self._timer = node.create_timer(period, self._check)

    def mark(self) -> None:
        """Call from the subscription callback."""
        self.count += 1
        if not self._announced:
            self._announced = True
            self.node.get_logger().info(
                f"{self.label}: first message on {self.topic}")
            self._timer.cancel()

    def _check(self) -> None:
        if self.count:
            self._timer.cancel()
            return

        publishers = self.node.count_publishers(self.topic)
        if publishers:
            # Publisher exists but nothing crosses: usually a QoS mismatch.
            self.node.get_logger().warn(
                f"{self.label}: {self.topic} has {publishers} publisher(s) but "
                f"no message has arrived. Check QoS with "
                f"`ros2 topic info -v {self.topic}` -- a RELIABLE subscriber "
                f"never receives from a BEST_EFFORT publisher, and neither "
                f"side reports it.")
            return

        available = self.same_type_topics()
        if available:
            self.node.get_logger().warn(
                f"{self.label}: NOTHING is publishing {self.topic}. These "
                f"{self.type_name} topics are being published right now -- the "
                f"name you want is probably one of them: {', '.join(available)}"
            )
        else:
            self.node.get_logger().warn(
                f"{self.label}: NOTHING is publishing {self.topic}, and no "
                f"{self.type_name} topic is being published at all. Is the "
                f"source running, and on the same ROS_DOMAIN_ID?")

    def same_type_topics(self) -> list:
        """Sorted topics of the watched type that have a publisher, excluding
        the watched topic itself."""
        out = []
        for name, types in self.node.get_topic_names_and_types():
            if self.type_name not in types or name == self.topic:
                continue
            if self.node.count_publishers(name):
                out.append(name)
        return sorted(out)
