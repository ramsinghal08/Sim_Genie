#!/usr/bin/env python3
"""
topicwatch.py -- say it out loud when a subscription never receives anything.

WHY THIS EXISTS
---------------
A ROS 2 subscription to a topic nobody publishes is completely silent. It is
not an error, it is not a warning, it is not even a debug line: the node comes
up, logs "subscribed to /unity/camera/image_raw", and then waits forever. From
the outside that is indistinguishable from a working rig with a slow camera,
and every downstream symptom points somewhere else -- an empty panel pane looks
like a browser problem, a missing score looks like a scoring bug.

It cost us a demo evening. Unity was publishing `/panther/camera_left/image_raw`
and every node in this rig was subscribed to `/unity/camera/image_raw`. Nothing
in any log said so.

There are exactly three reasons a subscription stays quiet, and they need
different fixes, so the watchdog distinguishes them:

    no publisher at all   -> the topic NAME is wrong (or the node is not
                             running, or ROS_DOMAIN_ID differs). Prints the
                             topics of the same type that DO exist, because the
                             right name is nearly always one of them.
    publishers, no data   -> a QoS mismatch, or the publisher is stalled. The
                             publisher's own `ros2 topic hz` looks fine.
    data, but not paired  -> only for synchronised pairs; see scoring_node.

USE
---
    self.watch = TopicWatchdog(self, "/unity/camera/image_raw",
                               "sensor_msgs/msg/Image", "camera")
    ...
    def _on_frame(self, msg):
        self.watch.mark()
"""

from __future__ import annotations


class TopicWatchdog:
    """Warn, on a timer, while `topic` has produced no messages.

    Goes quiet as soon as the first message arrives, and says so once. Costs
    one timer per watched topic and nothing at all on the message path beyond
    a boolean store.
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
        """Called from the subscription callback. Cheap on purpose."""
        self.count += 1
        if not self._announced:
            self._announced = True
            self.node.get_logger().info(
                f"{self.label}: first message on {self.topic}")
            self._timer.cancel()

    # ------------------------------------------------------------------

    def _check(self) -> None:
        if self.count:
            self._timer.cancel()
            return

        publishers = self.node.count_publishers(self.topic)
        if publishers:
            # The subscription exists, a publisher exists, and nothing crosses.
            # In this rig that is nearly always QoS: a BEST_EFFORT publisher
            # will not talk to a RELIABLE subscriber, and neither side logs it.
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
        """Topics of the watched type that currently have a publisher.

        Sorted, and the watched topic itself is excluded -- it is the thing
        that is missing, so listing it back is noise.
        """
        out = []
        for name, types in self.node.get_topic_names_and_types():
            if self.type_name not in types or name == self.topic:
                continue
            if self.node.count_publishers(name):
                out.append(name)
        return sorted(out)
