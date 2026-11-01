# Troubleshooting

Most failures in this system are silent: a mismatched topic name, QoS profile or timestamp produces no error, only an empty pane or a missing score. Start with:

```bash
./rig topics                                   # default topic names
./rig topics /panther/camera_left/image_raw    # custom camera topic
```

This lists each expected topic, whether anything publishes it, and the topics of the same type that do exist. It also reports whether ports 9090 (rosbridge) and 8080 (web_video_server) are in use.

If Unity publishes under different names, pass them through instead of editing code:

```bash
./rig mock unity_image_topic:=/panther/camera_left/image_raw labels_topic:=/your/label/camera
./rig ceiling unity_image_topic:=/panther/camera_left/image_raw
```

## Panel is blank or there is no score

- **No ground-truth label topic.** Scoring needs the semantic label image (`/unity/labels`, published by `DualCapture`). Without it `/score/iou` is never published and the director reports `no scores received`. Video panes work independently of scoring.
- **Second rosbridge instance.** `rosbridge_websocket` retries on `Address already in use` indefinitely, so the panel may connect to a stale instance from a previous run. `./rig mock` and `./rig sweep` detect this and do not start a second one.
- **Both topics flowing, no score.** `ApproximateTimeSynchronizer` matches on header stamps, not arrival time. Publishers using different clocks (or a zero stamp) produce a healthy stream on each topic and no matched pairs. `scoring_node` reports counts for each side.
- **Stale browser tab.** The panel is a single static file. `./rig panel` serves `Cache-Control: no-store`, appends the file mtime to the printed URL, and the page checks its own `Last-Modified` on load. If in doubt, force-reload with Ctrl+Shift+R. The header shows the `Last-Modified` of the page being viewed.

## Running without ground truth

With a camera topic but no label topic, no IoU is possible, and the rig reports that explicitly:

- `scoring_node` publishes the reason on `/score/detail` and nothing on `/score/iou`.
- The panel shows `n/a` with the reason.
- The director logs `NO SCORE (n=0)`, and `results.md` opens with `NO SCORES IN THIS RUN`.
- `results.csv` leaves unmeasured cells empty rather than writing `NaN`.

`json.dumps` emits a bare `NaN`, which is not valid JSON. NaN values are never serialised into status messages.

## Image transport

All image topics are raw `sensor_msgs/Image`. Compressed variants are published only when enabled explicitly (`publish_compressed:=true` on `x2_bridge_node` or `perception_node`; `rgbAsJpeg` / `labelAsPng` on Unity's `DualCapture`). A compressed topic enabled at one end only produces no frames and no error, so enable both ends together (`input_compressed`, `use_compressed`, `ground_truth_compressed` on the ROS side and the panel's image dropdown).

Raw rgb8 at 832x512 and 24 fps is roughly 30 MiB/s. If the link cannot carry it, lower `rgbFps` or the capture resolution, or enable the compressed pair at both ends.

**QoS.** Image publishers use RELIABLE and subscribers use BEST_EFFORT (`image_pub_qos` / `image_sub_qos` in `imgcodec.py`). A BEST_EFFORT publisher and a RELIABLE subscriber are incompatible and never connect; `rosbridge_server` and `web_video_server` both subscribe RELIABLE.

**web_video_server URLs.** Do not percent-encode the topic in the query string. `web_video_server` does not URL-decode parameters, so `%2Fperception%2Fmask` is rejected as an invalid topic name, and the error appears only in that node's stdout.

## Topic wiring

```
Unity DualCapture   ->  /unity/camera/image_raw     Image rgb8
                    ->  /unity/labels               Image rgb8
                    ->  /unity/rover_pose           PoseStamped

x2_bridge           <-  /unity/camera/image_raw
                    ->  /world_model/image_raw      Image rgb8
                    ->  /world_model/camera_info    CameraInfo
                    ->  /world_model/state          String (JSON)

perception          <-  /world_model/image_raw
                    ->  /perception/mask            Image rgb8

scoring             <-  /perception/mask + /unity/labels
                    ->  /score/iou + /score/detail
```

## Control panel ports

| Port | Service | Used by |
|---|---|---|
| 8000 | panel page (`./rig panel`) | browser |
| 9090 | `rosbridge_websocket` | the page, after connect |
| 8080 | `web_video_server` | MJPEG mode only |

`./rig panel --demo` runs `tools/fake_rosbridge.py`, which provides a synthetic scene, score and director status so the panel can be developed without ROS.

## Model entitlement error

```
could not exchange the API key for a token: 403 Forbidden
{"error":"requested model is not available to this API key"}
```

The key authenticated but is not entitled to the model `x2`. No session was opened and nothing was billed. Pass the identifier your key can use:

```bash
ros2 launch mars_rig stress_rig.launch.py mock:=false model:=<name>
```

## Reactor SDK notes (reactor-sdk 1.1.1)

```python
src = await reactor.publish_track("source")   # takes a track name, not a track object
src.push_frame(rgb)                           # synchronous; (h, w, 3) uint8 RGB

out = reactor.track("main_video")             # bind by name before connect()
@out.on_frame
def on_frame(frame): ...
```

- `push_frame` does no pacing; the caller owns the 24 fps clock.
- `reactor.tracks` is empty until capabilities arrive after `connect()`, so bind by name.
- Use `reactor_sdk.time_micros()` for timestamps, not `time.time()`.
- Generation starts once a non-empty prompt is set and source frames are flowing.

## Pitfalls that produce plausible but wrong results

- **Scoring perception against itself.** The scorer must use Unity's label camera as reference. Using a previous prediction measures temporal consistency and can report 1.0 for a failed stack.
- **Scoring an empty detection as 1.0.** Total failure must score 0 or be skipped.
- **Unsynchronised frames.** Pair frames with `ApproximateTimeSynchronizer`.
- **Lossy compression on labels.** JPEG bleeds colour across class boundaries. Use raw or PNG.
- **Interpolating class ids.** Resize labels with nearest-neighbour only.
- **Naming protected objects in a prompt.** Naming the rover caused every ArUco marker to be rendered as a robot. Protect by count and stillness instead.
- **Logging one call site at two severities.** rclpy raises `ValueError: Logger severity cannot be changed between calls`. Use `roslog.emit()`.
- **Two OpenCV installs.** A pip-installed OpenCV 5 shadows the system OpenCV that ROS was built against and breaks `cv_bridge` with `KeyError: 16`. `mars_rig` avoids `cv_bridge`; `./rig doctor` reports a shadowing OpenCV.
- **Offline commands and ROS.** Sourcing ROS puts its site-packages on `PYTHONPATH`, which a venv does not shadow, so pytest may load ROS plugins and fail. `./rig` strips `PYTHONPATH`, `ROS_DISTRO` and `AMENT_PREFIX_PATH` for the offline path.
