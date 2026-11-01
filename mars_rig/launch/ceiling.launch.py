"""ceiling.launch.py: run perception on the plain Unity render and score it
against Unity's own labels (no X2, no prompts, no credits).

The IoU reported is the ceiling for every world-model result; if it is low,
perception or label alignment is broken.

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
        # Raw by default; true only if <unity_image_topic>/compressed is published.
        DeclareLaunchArgument("unity_compressed", default_value="false"),
        Node(
            package="mars_rig", executable="perception_node",
            name="perception_node", output="screen",
            parameters=[{
                # Straight from Unity, bypassing the world model.
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
