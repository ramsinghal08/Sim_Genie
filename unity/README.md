# Unity side: setup

The Unity project lives outside this repo. These are the scripts that go into
it, plus how to wire them up.

## Contents

```
Assets/MarsRig/
  Scripts/
    MarsClasses.cs     class palette (must match labels.py)
    SemanticLabel.cs   tags a renderer with its class
    DualCapture.cs     renders RGB + labels from one camera and publishes both
    RoverPath.cs       fixed scripted path, and /unity/rover_pose
    RosTime.cs         ROS 2 timestamps from the wall clock
  Editor/
    SceneBuilder.cs    Mars Rig > Build Scored Scene / Build Calibration Scene
    AlignmentCheck.cs  Mars Rig > Check Camera Alignment
```

**Status:** these scripts have not yet been opened in the Unity editor. They
compile against stub types whose signatures were checked against the
ROS-TCP-Connector source, but an editor build may still need small fixes.

## Setup

1. **Add ROS-TCP-Connector.** Window > Package Manager > + > Add package from
   git URL:

   ```
   https://github.com/Unity-Technologies/ROS-TCP-Connector.git?path=/com.unity.robotics.ros-tcp-connector
   ```

2. **Set the protocol to ROS 2.** Robotics > ROS Settings > Protocol = `ROS2`.
   This is required: `builtin_interfaces/Time` declares `int sec` under the
   ROS2 scripting define and `uint sec` without it, so `RosTime.cs` does not
   compile in ROS 1 mode. Set ROS IP to `127.0.0.1` when Unity and ROS run on
   the same machine.

3. **Copy `Assets/MarsRig/` into the project's `Assets/`.**

4. **Start the ROS side:**

   ```bash
   git clone -b main-ros2 https://github.com/Unity-Technologies/ROS-TCP-Endpoint.git src/ROS-TCP-Endpoint
   colcon build && source install/setup.bash
   ros2 run ros_tcp_endpoint default_server_endpoint --ros-args -p ROS_IP:=127.0.0.1
   ```

5. **Build the scene:** `Mars Rig > Build Scored Scene`. Save it.

6. **Press Play, then run `Mars Rig > Check Camera Alignment`.** See the next
   section for how to read the result.

## Alignment check

The RGB and label images must be pixel-aligned; if they are not, every
downstream metric is invalid.

`DualCapture` renders both passes from one camera in one frame. Two separate
cameras can drift apart (a changed transform, FOV or aspect ratio, or renders
on different frames while the rover moves) without raising any error. With one
camera rendered twice, the transform and projection are shared and both
renders occur within a single `LateUpdate`.

`Mars Rig > Check Camera Alignment` verifies this and catches other problems:

| Result | Meaning |
|---|---|
| `ALIGNMENT PASS` (at least 85% of label edges sit on an RGB edge) | Good; proceed. |
| `ALIGNMENT FAIL` | Stop; downstream results are not meaningful. |
| "N% of label pixels are not a palette colour" | The label pass is tinted by fog, post-processing, or a label material that is not `Unlit/Color`. |
| "obstacle is only N% of the frame" | Rocks are too small or distant; obstacle IoU will be noisy. |

It writes `AlignmentCheck/camera1_rgb.png`, `camera2_labels.png` and
`overlay.png`. Inspect `overlay.png` to confirm rocks coincide in both views,
and include it in the write-up.

## Topics published

| Topic | Type | Rate | Note |
|---|---|---|---|
| `/unity/camera/image_raw` | `Image` (rgb8, raw) | 24 Hz | matches X2's source pacing |
| `/unity/labels` | `Image` (rgb8, raw) | 10 Hz | the scorer caps at 10 Hz |
| `/unity/rover_pose` | `PoseStamped` | 10 Hz | |

Both image topics are raw, which is what subscribers in the rig read by
default. A `<topic>/compressed` companion is a separate topic: if Unity
publishes only one of the pair and a node subscribes to the other, they never
connect, with no frames and no error.

Raw RGB at 24 fps is about 30 MiB/s at 832x512 and can saturate TCP and DDS;
the publisher may report 24 Hz while the subscriber sees far less. Verify with
`ros2 topic hz` from the ROS side, not from Unity. If the link cannot carry
it, reduce `rgbFps` or the capture resolution first.

Two optional compressed transports exist. Each requires a matching setting on
the ROS side:

| Unity setting | Publishes | Also set |
|---|---|---|
| `rgbAsJpeg` | `/unity/camera/image_raw/compressed` (jpeg) | `x2_bridge_node -p input_compressed:=true`, and the panel's *images* dropdown |
| `labelAsPng` | `/unity/labels/compressed` (png) | `scoring_node -p ground_truth_compressed:=true` |

PNG is lossless, so classes survive exactly. Do not use JPEG for labels: it
bleeds colour across class boundaries. The scorer logs an error if it receives
JPEG ground truth.

## Label colours

`MarsClasses.Palette` must match `DEFAULT_PALETTE` in
`mars_rig/mars_rig/labels.py`:

```
unlabelled (0,0,0)   drivable (0,255,0)   obstacle (255,0,0)   sky (0,0,255)
```

A mismatch raises no error: the scorer assigns each pixel to the nearest
palette colour, so it would map rocks to the wrong class and report a
plausible but meaningless IoU. If you change a colour, change it in both
places, or pass the new palette to `scoring_node` as `palette_json`.

## Scenes

`Build Scored Scene` contains no ArUco markers and no traffic cone. Use it for
all measurements. `Build Calibration Scene` includes them for a single
structure check and must not be used for sweeps.

## Determinism

The scene is built from a fixed seed, and the rover's pose is a pure function
of elapsed time rather than an integration of `Time.deltaTime`. Identical
geometry and motion across runs ensure that a score change is attributable to
the visual condition alone.
