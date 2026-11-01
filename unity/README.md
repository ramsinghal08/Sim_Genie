# Unity side - setup

The Unity project lives outside this repo. These are the scripts that go in it,
plus how to wire them up.

## What is here

```
Assets/MarsRig/
  Scripts/
    MarsClasses.cs     the class palette - MUST match labels.py
    SemanticLabel.cs   tags a renderer with its class
    DualCapture.cs     renders RGB + labels from ONE camera and publishes both
    RoverPath.cs       the fixed scripted path, and /unity/rover_pose
    RosTime.cs         ROS 2 timestamps from the wall clock
  Editor/
    SceneBuilder.cs    Mars Rig > Build Scored Scene / Build Calibration Scene
    AlignmentCheck.cs  Mars Rig > Check Camera Alignment
```

**These have never been opened in Unity.** They compile clean against stub types
whose signatures were verified against the real ROS-TCP-Connector source, but
that is not the same as building in the editor. Expect to fix something.

## Setup

1. **Add ROS-TCP-Connector.** Window > Package Manager > + > Add package from
   git URL:

   ```
   https://github.com/Unity-Technologies/ROS-TCP-Connector.git?path=/com.unity.robotics.ros-tcp-connector
   ```

2. **Set the protocol to ROS 2.** Robotics > ROS Settings > Protocol = `ROS2`.
   This is not optional: `builtin_interfaces/Time` declares `int sec` under the
   ROS2 scripting define and `uint sec` without it, so `RosTime.cs` will not
   compile in ROS 1 mode. Set ROS IP to `127.0.0.1` - Unity and ROS are on the
   same box.

3. **Copy `Assets/MarsRig/` into the project's `Assets/`.**

4. **Start the ROS side:**

   ```bash
   git clone -b main-ros2 https://github.com/Unity-Technologies/ROS-TCP-Endpoint.git src/ROS-TCP-Endpoint
   colcon build && source install/setup.bash
   ros2 run ros_tcp_endpoint default_server_endpoint --ros-args -p ROS_IP:=127.0.0.1
   ```

5. **Build the scene:** `Mars Rig > Build Scored Scene`. Save it.

6. **Press Play, then run `Mars Rig > Check Camera Alignment`.** Read the next
   section before you do anything else with the output.

## The check that comes before everything else

The requirement: *"If this fails, every number downstream is wrong."*

`DualCapture` renders the RGB pass and the label pass from **one camera, in one
frame**. That is deliberate. Two camera objects can drift apart in six different
ways - a nudged transform, a changed FOV, a different aspect ratio, or the two
rendering on different frames while the rover is moving - and none of them throw
an exception. One camera rendered twice cannot drift: the transform and the
projection are literally the same object, and both renders happen inside one
`LateUpdate`.

`Mars Rig > Check Camera Alignment` confirms that rather than assuming it, and
catches what the single-camera design does not:

| Symptom it reports | What it means |
|---|---|
| `ALIGNMENT PASS` - ≥85% of label edges sit on an RGB edge | good, proceed |
| `ALIGNMENT FAIL` | stop; nothing downstream means anything yet |
| *"N% of label pixels are not a palette colour"* | the label pass is being tinted - fog, post-processing, or a label material that is not `Unlit/Color` |
| *"obstacle is only N% of the frame"* | rocks too small or too distant; obstacle IoU will be noise |

It writes `AlignmentCheck/camera1_rgb.png`, `camera2_labels.png` and
`overlay.png`. Open `overlay.png` and confirm rocks land in the same place -
that is the human half of the check, and it belongs in the write-up.

## Topics published

| Topic | Type | Rate | Note |
|---|---|---|---|
| `/unity/camera/image_raw` | `Image` (rgb8, raw) | 24 Hz | X2's source pacing |
| `/unity/labels` | `Image` (rgb8, raw) | 10 Hz | the scorer caps at 10 Hz anyway |
| `/unity/rover_pose` | `PoseStamped` | 10 Hz | |

Both image topics are **raw**, because that is what every subscriber in the rig
reads by default. A `<topic>/compressed` companion is a *different topic*: if
Unity publishes only one of the pair and a node subscribes to the other, the two
never connect - no frames, no error, an empty panel pane, and `ros2 topic hz` on
the Unity side still reporting a healthy 24.

Raw RGB at 24 fps is ~30 MiB/s at 832×512 and can choke TCP and DDS - it shows
up as the publisher reporting 24 Hz while the subscriber sees 3 Hz, with no
error anywhere. Verify with `ros2 topic hz` **from the ROS side**, never from
Unity. If the link cannot carry it, drop `rgbFps` or the capture resolution
first; those change nothing else in the graph.

Two optional compressed transports exist, and each is a *pair* of settings that
must be flipped together or the topic goes nowhere:

| Unity setting | Publishes | Also set |
|---|---|---|
| `rgbAsJpeg` | `/unity/camera/image_raw/compressed` (jpeg) | `x2_bridge_node -p input_compressed:=true`, and the panel's *images* dropdown |
| `labelAsPng` | `/unity/labels/compressed` (png) | `scoring_node -p ground_truth_compressed:=true` |

PNG is lossless, so classes survive it exactly. **Never JPEG on labels** - it
bleeds colour across class boundaries and nothing downstream can undo it. The
scorer logs an error if it ever receives JPEG ground truth.

## Label colours

`MarsClasses.Palette` must match `DEFAULT_PALETTE` in
`mars_rig/mars_rig/labels.py`:

```
unlabelled (0,0,0)   drivable (0,255,0)   obstacle (255,0,0)   sky (0,0,255)
```

A mismatch does not throw. The scorer assigns each pixel to the nearest palette
colour, so it quietly maps rocks to the wrong class and reports a plausible,
meaningless IoU. If you change a colour, change it in both places - or pass the
new palette to `scoring_node` as `palette_json` and leave the file alone.

## Two scenes, on purpose

`Build Scored Scene` has **no ArUco markers and no traffic cone**. Naming the
rover in a prompt once turned every marker in the scene into a robot, and
hundreds of them started moving. `Build Calibration Scene` has
them, for **one** structure measurement. Never sweep against the calibration
scene.

## Determinism

The scene is built from a fixed seed and the rover's pose is a pure function of
elapsed time, not an integration of `Time.deltaTime`. Both matter: identical
geometry and identical motion are what make a score change attributable to the
visual condition alone. If two runs see different rocks at different distances,
part of the IoU difference is geometry and there is no way to separate the two
afterwards.
