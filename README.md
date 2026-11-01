# Sim Genie: Martian Perception Stress-Test Rig

A reproducible pipeline for measuring when a rover's perception stack fails under Martian conditions.

Unity owns physics, collision and ground truth. A world model (Reactor X2) repaints Unity's frames into photorealistic Mars, changing appearance but not geometry. The perception stack runs on the repainted frames unmodified, a scorer compares its output against Unity's labels, and a director escalates the conditions until perception breaks.

The output is a catalogue of failure conditions with numbers (IoU per class and condition), not a better rover.

## Pipeline

```
Unity (RGB + semantic labels)
   |
   v
x2_bridge_node --> Reactor X2 --> /world_model/image_raw
                                        |
                                        v
                                perception_node --> /perception/mask
                                                          |
Unity labels --------------------------------------> scoring_node --> /score/iou
                                                          ^
                                                          |
                                              prompt_director (escalates conditions)
```

## Repository layout

```
mars_rig/            ROS 2 Jazzy Python package
  mars_rig/
    labels.py          palette, quantisation, IoU          (no rclpy)
    perception.py      baseline segmentation               (no rclpy)
    director_core.py   phase machine and results writer    (no rclpy)
    synthetic_scene.py stand-in for Unity, for offline runs (no rclpy)
    *_node.py          ROS wrappers around the modules above
  config/              prompt catalogue (45 prompts, 8 families)
  launch/              launch files
  test/                pytest suite, runs without ROS
tools/               offline loop, fake rosbridge, panel server
panel/               browser control panel (single-file, plain JS)
unity/               C# scripts for the Unity project (see unity/README.md)
World_Hack/my-world/ Next.js reference client for Reactor X2
docs/                troubleshooting and design notes
rig                  single entry point for install, build and run
```

The core logic lives in the modules marked `no rclpy`; the nodes are thin wiring. This is what allows the full loop to be tested without a ROS graph.

## Quick start (no ROS, Unity or API key required)

```bash
./rig install --offline-only   # creates a local .venv
./rig test                     # unit tests
./rig offline                  # full loop on a synthetic scene
./rig panel --demo             # control panel at http://localhost:8000/index.html
```

`./rig offline` walks all 23 conditions and writes `results/offline/results.csv` and `results.md`. These files are marked **SIMULATED**: the synthetic degradation is not X2, so they validate the loop, not the measurements.

Run `./rig doctor` to see what is installed and what is missing. `./rig help` lists every command.

## Running with ROS 2 and Unity

Requires ROS 2 Jazzy, Unity with the ROS-TCP-Connector package, and a Reactor API key for live runs.

```bash
./rig install          # python deps, reactor-sdk, ROS 2 Jazzy, TCP endpoint, build
./rig endpoint         # ROS-TCP endpoint for Unity (leave running)
./rig topics           # check that expected topics have publishers
./rig ceiling          # score plain Unity renders against labels (upper bound)
./rig mock             # full graph with a mock bridge (no credits)
./rig sweep            # live run against X2 (needs REACTOR_API_KEY)
```

Recommended order:

1. Start the endpoint and run the Unity scene (`unity/README.md`).
2. Confirm frames arrive with `ros2 topic hz /unity/camera/image_raw` and `/unity/labels`.
3. Run `Mars Rig > Check Camera Alignment` in Unity. If it fails, later numbers are invalid.
4. Run `./rig ceiling`. It bounds every result that follows.
5. Run `./rig mock` to verify the whole graph without spending credits.
6. Run `./rig sweep`. It asks for confirmation, then closes the session on exit.

## Metrics

`/score/iou` is the mean IoU over the `drivable` and `obstacle` classes (`sky` is excluded because it is large and easy, which would inflate the mean). Because drivable terrain is easy to segment, the mean rarely falls below about 0.45 even when obstacles are not detected at all. Report the obstacle IoU alongside the mean; `results.csv` and `results.md` include per-class columns for this reason.

Runs without ground truth report the score as unavailable (empty cells in the CSV) rather than a placeholder value.

## Status

| Component | State |
|---|---|
| IoU maths, director sweep and adaptive modes, reporting | unit tested |
| Offline loop (23 conditions) | run end to end |
| Control panel | tested in Chromium against a fake rosbridge |
| ROS nodes and `./rig` ROS path | not yet run against a live ROS graph |
| Unity scripts | compile against stubs, not yet opened in the editor |
| Bridge against a live X2 session | not yet run |
| Camera alignment | not yet measured |

No IoU value produced so far should be treated as a measurement.

## Open design decisions

- **Perception model.** `perception_node` runs `baseline_cv`, a classical segmenter. A COCO-trained model does not drop in, because its classes do not map to the drivable/obstacle labels without a remap or retraining.
- **Metric scope.** Traversability IoU only; no depth metric.
- **Browser image transport.** The panel supports both `web_video_server` (MJPEG) and raw `sensor_msgs/Image` over rosbridge, selectable at runtime.
- **Prompt language.** The catalogue has English and Chinese prompts; `lang` defaults to `zh` and has not been A/B tested.

## Further reading

- [`docs/troubleshooting.md`](docs/troubleshooting.md): blank panel, missing scores, QoS, topic wiring, and other silent failure modes.
- [`unity/README.md`](unity/README.md): Unity setup and the camera alignment check.
- [`World_Hack/my-world/README.md`](World_Hack/my-world/README.md): the X2 reference client.
