"""ceiling.launch.py -- the sanity check to run first.

Perception on the PLAIN Unity render, scored against Unity's own labels. No X2,
no prompts, no credits. Whatever IoU this reports is the CEILING: every
world-model number is measured below it, and if it is low then perception or
label alignment is broken and nothing downstream means anything.

    ros2 launch mars_rig ceiling.launch.py
    ros2 topic echo /score/detail --once
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    arg = LaunchConfiguration
    return LaunchDescription([
        DeclareLaunchArgument("unity_image_topic",
                              default_value="/unity/camera/image_raw"),
        DeclareLaunchArgument("labels_topic", default_value="/unity/labels"),
        DeclareLaunchArgument("perception_method", default_value="baseline_cv"),
        # Raw, to match what Unity's DualCapture publishes. Set true only
        # alongside a real <unity_image_topic>/compressed publisher -- pointing
        # perception at a topic nobody publishes reports nothing at all.
        DeclareLaunchArgument("unity_compressed", default_value="false"),
        Node(
            package="mars_rig", executable="perception_node",
            name="perception_node", output="screen",
            parameters=[{
                # Straight off Unity, bypassing the world model entirely.
                "input_topic": arg("unity_image_topic"),
                "use_compressed": arg("unity_compressed"),
                "method": arg("perception_method"),
            }],
        ),
        Node(
            package="mars_rig", executable="scoring_node", name="scoring_node",
            output="screen",
            parameters=[{"ground_truth_topic": arg("labels_topic")}],
        ),
    ])
