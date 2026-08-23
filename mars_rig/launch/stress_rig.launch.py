"""stress_rig.launch.py -- the whole graph, in one command.

    # BUILD_SPEC 8, test 6: full loop, mock bridge, no credits, no Unity
    ros2 launch mars_rig stress_rig.launch.py

    # test 4: director against a fake score, nothing else running
    ros2 launch mars_rig stress_rig.launch.py fake_score:=true perception:=false

    # test 8: the real sweep
    export REACTOR_API_KEY=rk_...
    ros2 launch mars_rig stress_rig.launch.py \
        mock:=false settle_sec:=8.0 dwell_sec:=25.0 keep_backlog:=true

Every optional piece is a launch argument, because rosbridge_server and
web_video_server are separate apt packages: a launch file that hard-requires
them fails as a whole on a machine that has not installed them, and that is not
a failure anyone wants to debug at 18:00 on demo night.
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.conditions import IfCondition, UnlessCondition
from launch.substitutions import LaunchConfiguration, PythonExpression
from launch_ros.actions import Node

DEFAULT_CATALOG = os.path.join(
    get_package_share_directory("mars_rig"), "config", "prompt_catalog_x2.json")


def generate_launch_description():
    arg = LaunchConfiguration

    args = [
        DeclareLaunchArgument("mock", default_value="true",
                              description="X2 bridge in mock mode -- no API key, no credits"),
        DeclareLaunchArgument("fake_score", default_value="false",
                              description="publish a synthetic score instead of scoring real frames"),
        DeclareLaunchArgument("perception", default_value="true"),
        # false when something ELSE is publishing /world_model/image_raw --
        # notably World_Hack/my-world, which bridges X2 through a browser
        # because the Python SDK path cannot authenticate to the model.
        # Two publishers on that topic is not a fallback, it is interleaved
        # frames: perception segments whichever arrived last and nothing
        # anywhere reports a conflict.
        DeclareLaunchArgument("bridge", default_value="true",
                              description="run x2_bridge_node; false when the "
                                          "browser app is the bridge"),
        DeclareLaunchArgument("director", default_value="true"),
        DeclareLaunchArgument("rosbridge", default_value="true",
                              description="needed by panel/index.html"),
        DeclareLaunchArgument("web_video_server", default_value="true",
                              description="one of the two BUILD_SPEC 9.5 image transports"),
        DeclareLaunchArgument("mode", default_value="sweep",
                              description="sweep | adaptive | llm"),
        DeclareLaunchArgument("lang", default_value="zh"),
        DeclareLaunchArgument("settle_sec", default_value="8.0"),
        DeclareLaunchArgument("dwell_sec", default_value="25.0"),
        DeclareLaunchArgument("keep_backlog", default_value="true",
                              description="true for scoring runs, false for the live demo (9.7)"),
        # Adaptive's two thresholds. Worth exposing because the right values
        # depend on the ceiling, which is not known until Unity is measured: if
        # escalate_above sits at or above the ceiling the ladder never advances,
        # and the director says so on its first row.
        DeclareLaunchArgument("escalate_above", default_value="0.75",
                              description="adaptive: keep climbing while IoU is above this"),
        DeclareLaunchArgument("break_below", default_value="0.50",
                              description="adaptive: stop once IoU falls below this"),
        DeclareLaunchArgument("catalog", default_value=DEFAULT_CATALOG),
        DeclareLaunchArgument("out_dir", default_value="results"),
        DeclareLaunchArgument("unity_image_topic",
                              default_value="/unity/camera/image_raw"),
        DeclareLaunchArgument("unity_compressed", default_value="false",
                              description="true ONLY if something publishes "
                                          "<unity_image_topic>/compressed; "
                                          "DualCapture publishes raw Image"),
        DeclareLaunchArgument("labels_topic", default_value="/unity/labels"),
        DeclareLaunchArgument("perception_method", default_value="baseline_cv"),
        DeclareLaunchArgument("initial_prompt", default_value=""),
        # The model identifier the API key is entitled to. Exposed because a
        # key without access to this exact name fails at connect with
        #     403 Forbidden -- {"error":"requested model is not available to
        #     this API key"}
        # which is an entitlement problem, not a code one, and the fix is a
        # different string -- not worth a rebuild.
        DeclareLaunchArgument("model", default_value="x2",
                              description="Reactor model name; must be one "
                                          "this API key can use"),
    ]

    nodes = [
        Node(
            package="mars_rig", executable="x2_bridge_node", name="x2_bridge",
            output="screen",
            condition=IfCondition(arg("bridge")),
            parameters=[{
                "mock": arg("mock"),
                "model_name": arg("model"),
                "input_image_topic": arg("unity_image_topic"),
                "input_compressed": arg("unity_compressed"),
                "keep_backlog": arg("keep_backlog"),
                "initial_prompt": arg("initial_prompt"),
            }],
        ),
        Node(
            package="mars_rig", executable="perception_node",
            name="perception_node", output="screen",
            condition=IfCondition(arg("perception")),
            parameters=[{"method": arg("perception_method")}],
        ),
        # The scorer is the only node that sees the label camera. Perception is
        # deliberately not given the topic (BUILD_SPEC 2).
        Node(
            package="mars_rig", executable="scoring_node", name="scoring_node",
            output="screen",
            condition=UnlessCondition(arg("fake_score")),
            parameters=[{"ground_truth_topic": arg("labels_topic")}],
        ),
        Node(
            package="mars_rig", executable="fake_score_publisher",
            name="fake_score_publisher", output="screen",
            condition=IfCondition(arg("fake_score")),
        ),
        Node(
            package="mars_rig", executable="prompt_director",
            name="prompt_director", output="screen",
            condition=IfCondition(arg("director")),
            parameters=[{
                "mode": arg("mode"),
                "lang": arg("lang"),
                "catalog": arg("catalog"),
                "settle_sec": arg("settle_sec"),
                "dwell_sec": arg("dwell_sec"),
                "out_dir": arg("out_dir"),
                "escalate_above": arg("escalate_above"),
                "break_below": arg("break_below"),
            }],
        ),
        Node(
            package="rosbridge_server", executable="rosbridge_websocket",
            name="rosbridge_websocket", output="screen",
            condition=IfCondition(arg("rosbridge")),
        ),
        # rosbridge_websocket_launch.xml normally brings this up alongside
        # rosbridge_websocket; launching the bare node here skipped it. Without
        # it, /rosapi/topics_for_type and /rosapi/publishers don't exist, so
        # the panel's connect() still succeeds and the websocket looks fine,
        # but its entire "why is this pane empty" diagnosis (diagnosePanes())
        # goes silently quiet forever -- panel/index.html's own comment says
        # so: "Silently unavailable if rosapi is not running". That is the
        # single most useful feature this rig has for the exact failure it
        # warns about everywhere else (README: "a subscription to an
        # unpublished topic is completely silent"), so it must not be the one
        # piece left out.
        Node(
            package="rosapi", executable="rosapi_node",
            name="rosapi", output="screen",
            condition=IfCondition(arg("rosbridge")),
        ),
        Node(
            package="web_video_server", executable="web_video_server",
            name="web_video_server", output="screen",
            condition=IfCondition(arg("web_video_server")),
        ),
    ]
    return LaunchDescription(args + nodes)
