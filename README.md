# Martian Perception Stress-Test Rig

We can't take our rover to Mars, so we bring Mars to the rover — and then make
it worse on purpose, until perception breaks.

Unity owns physics, collision and ground truth. Reactor X2 repaints Unity's
frames into photorealistic Mars — appearance only, not geometry. Our perception
stack runs on the repaint, unmodified. A scorer compares its output against
Unity's labels, and a director escalates conditions until perception fails.

The deliverable is **not a better rover**. It is a reproducible catalogue of the
conditions under which our perception fails, with numbers.

See `BUILD_SPEC.md` for the full spec. This file is how to run it.

---

## Layout

```
mars_rig/                 ROS 2 Jazzy python package (colcon)
  mars_rig/
    x2_bridge_node.py     Unity frames -> X2 `source`; `main_video` -> ROS
    perception_node.py    /world_model/image_raw -> /perception/mask
    scoring_node.py       IoU(prediction, Unity labels) -> /score/iou
    prompt_director.py    reads the score, picks the next condition
    fake_score_publisher.py   a score with no pipeline behind it
    labels.py             palette, quantisation, IoU maths   (no rclpy)
    perception.py         the segmentation itself            (no rclpy)
    director_core.py      phase machine + results writer     (no rclpy)
    synthetic_scene.py    a stand-in Unity, for rehearsing   (no rclpy)
  config/prompt_catalog_x2.json    45 prompts, 8 families, 23-step ladder
  launch/                 stress_rig.launch.py, ceiling.launch.py
  test/                   pytest, runs with no ROS installed
tools/offline_loop.py     the whole loop with no ROS, no Unity, no credits
rig                       install / build / run — the single entry point
panel/index.html          landing page ("Sim Genie")
panel/control.html        control panel (rosbridge, plain JS, single file)
unity/Assets/MarsRig/     Unity C# — copy into the Unity project (see unity/README.md)
results/                  results.csv, results.md, bags
```

The four `no rclpy` modules are where the logic lives. The nodes are wiring
around them. That split is what lets the rig be tested without a ROS graph.

---

## Run it

Everything goes through one script. `./rig help` lists the lot.

```bash
./rig install      # python deps, reactor-sdk, ROS 2 Jazzy, TCP endpoint, build
./rig test         # 28 tests, ~1s
./rig offline      # the whole loop, no ROS, no Unity, no credits
```

`./rig doctor` says what is present and what is missing, and each line names the
command that fixes it. Run it first if anything behaves oddly.

The offline commands run with `PYTHONPATH`, `ROS_DISTRO` and `AMENT_PREFIX_PATH`
stripped, deliberately. Sourcing ROS puts its site-packages on `PYTHONPATH`, and
a venv does **not** shadow that — so in a ROS-sourced shell the venv's pytest
discovers ROS's `launch_testing` plugin and dies on `import yaml`, which lives
in the ROS environment and not in the venv. Nothing in the offline path needs
ROS, so `./rig` does not let it in.

### Nothing installed — no ROS, no Unity, no API key

```bash
./rig install --offline-only   # no sudo, no ROS: just a .venv
./rig test
./rig offline
./rig panel                    # http://localhost:8000/index.html -> control.html
```

`./rig offline` walks all 23 conditions in about a minute and writes
`results/offline/results.csv` and `results.md`. Those files are stamped
**SIMULATED** in their first line, because they are: `synthetic_scene.degrade()`
is not X2, and it proves the loop, not the numbers.

Extra arguments pass straight through, so the modes are reachable too:

```bash
./rig offline --mode adaptive --escalate-above 0.55
./rig test -k iou
```

### With ROS 2 Jazzy

```bash
./rig endpoint         # ros_tcp_endpoint for Unity -- leave this running
./rig mock             # the full graph, mock bridge, no credits
./rig director-test    # director against a fake score (BUILD_SPEC 8, test 4)
./rig ceiling          # the REAL ceiling, off Unity -- run this first
./rig sweep            # the live run; asks before spending credits
```

`./rig sweep` refuses to start without `REACTOR_API_KEY`, states the
one-session constraint, and asks for confirmation. It shuts the session down on
exit and on Ctrl-C, because credits bill per second.

Open the panel with `./rig panel` and connect it to `ws://localhost:9090` —
`./rig mock` starts a rosbridge for it.

## The control panel

```bash
./rig panel --demo        # no ROS needed at all
```

Three ports are involved and they are all different things:

| Port | What | Who talks to it |
|---|---|---|
| **8000** | the panel page itself (`./rig panel`) | your browser |
| **9090** | `rosbridge_websocket` — the ROS data feed | the page, after **connect** |
| **8080** | `web_video_server` — MJPEG image streams | the page's `<img>` tags, MJPEG mode only |

Then open **http://localhost:8000/index.html** — a landing page ("Sim Genie")
with an **Open Control Panel** button through to `control.html`, where you put
`ws://localhost:9090` in the box, press **connect**, and set *images* to
**rosbridge raw Image**.

`--demo` runs `tools/fake_rosbridge.py`, which speaks enough of the rosbridge
protocol to feed the page a synthetic scene, a live score and director status.
It exists so the panel can be built, styled and rehearsed before Unity or a paid
session are anywhere near ready — and so a panel regression takes ten seconds to
catch instead of showing up at 18:00 on demo night.

Against the real graph, drop `--demo` and start `./rig mock` (or `./rig sweep`)
in another terminal — those launch the rosbridge the page connects to.

**Images reach the browser two ways** (BUILD_SPEC 9.5 is deliberately still
open, so both are built and the choice is a dropdown):

| Setting | Needs | Notes |
|---|---|---|
| `web_video_server (MJPEG)` | the `web_video_server` node | lower latency, no websocket traffic. Two details decide whether it works at all: `default_transport=raw` (ask for `compressed` and it subscribes to `<topic>/compressed`, which nothing here publishes), and the topic must go in the URL **un-encoded** — see below |
| `rosbridge raw Image` | nothing extra | subscribes to the raw `sensor_msgs/Image` topic and paints the pixels into a `<canvas>`. Every frame crosses the websocket as JSON. Works with `--demo` |

**Never percent-encode the topic in a web_video_server URL.** `web_video_server`
does not URL-decode its query parameters, so `encodeURIComponent` turns
`/perception/mask` into `%2Fperception%2Fmask`, which reaches rclcpp as a
literal topic name and is rejected:

```
topic name must not contain characters other than alphanumerics, '_', '~',
'{', or '}': '%2Fperception%2Fmask'
```

The stream then errors and every MJPEG pane is black. The error only appears in
`web_video_server`'s own stdout — not in the browser, not in the panel. ROS
topic names are already restricted to characters that are safe in a query
value, so there is nothing to escape.

**Every image topic in this rig is raw `sensor_msgs/Image`.** No node publishes a
`<topic>/compressed` companion unless it is explicitly told to
(`publish_compressed:=true` on `x2_bridge_node` or `perception_node`,
`rgbAsJpeg` / `labelAsPng` on Unity's `DualCapture`). A compressed topic that
exists on one side of the graph and not the other is the quietest failure in the
whole system — the subscriber connects to nothing, `ros2 topic hz` on the
publisher still looks healthy, and the pane is simply empty. If you turn a
compressed transport on, turn it on at **both** ends in the same change:
`input_compressed` / `use_compressed` / `ground_truth_compressed` on the ROS
side and the panel's *images* dropdown on the browser side.

## Is the page in front of you the current one?

The panel is one static file, and a browser tab left open across an edit is a
liar: it keeps the OLD DOM while staying connected to a live rosbridge, so it
goes on updating the director table from real messages and goes on showing
every symptom you just fixed. Nothing on screen says so, and the obvious
conclusion — "the fix did not work" — is wrong.

Two checks, both instant:

- The header's right-hand corner shows **`page <date time>`** — the
  `Last-Modified` of the document you are actually looking at. Older than your
  last `git pull`? You are looking at a stale page.
- The *images* dropdown reads **`rosbridge raw Image`** in the current version.
  If it says `rosbridge base64`, the page predates the raw-transport change.

Three things stop it happening, because one is not enough:

- `./rig panel` serves `Cache-Control: no-store` and strips conditional request
  headers (`tools/panel_server.py`). `python -m http.server` sends no caching
  directives at all and answers conditional requests with `304`.
- The printed link carries the file's mtime (`index.html?v=…`), so a changed
  file is a changed URL.
- **The page checks itself.** `no-store` alone does not save you: with no
  caching directives — which is what the old server sent — a browser applies
  *heuristic* freshness from `Last-Modified` and answers a navigation **from
  cache without contacting the server**, so the new header is never seen. That
  is how a page four commits old survived a `git pull`, a server restart and a
  reload. On load the page now HEADs its own URL with `cache: "no-store"`,
  compares the server's `Last-Modified` against its own, and reloads once
  through a cache-busting URL if the file on disk is newer.

If you ever suspect it anyway: **Ctrl+Shift+R** forces revalidation.

## The panel is blank / there is no score

Run this first. It is the whole diagnosis in one command:

```bash
./rig topics                                   # default names
./rig topics /panther/camera_left/image_raw    # or whatever Unity publishes
```

It prints every topic the rig expects, whether anything is publishing it, and —
when nothing is — **the topics of the same type that DO exist**, which is almost
always where the right name is. It also reports whether ports 9090 (rosbridge)
and 8080 (web_video_server) are in use.

**The failure this exists for: a subscription to a topic nobody publishes is
completely silent.** No error, no warning, not even at debug. The node logs
"subscribed", waits forever, and every symptom shows up somewhere else — a blank
pane looks like a browser bug, a missing score looks like a scoring bug. We lost
an evening to exactly this: Unity was publishing `/panther/camera_left/image_raw`
and every node here was subscribed to `/unity/camera/image_raw`.

Point the rig at the real names — nothing needs editing:

```bash
./rig mock unity_image_topic:=/panther/camera_left/image_raw \
           labels_topic:=/your/label/camera
./rig ceiling unity_image_topic:=/panther/camera_left/image_raw
```

Three other things that produce exactly this picture:

- **No ground-truth label topic.** The camera can be perfect and there is still
  no IoU: scoring needs a *semantic label* image (`/unity/labels`) as well as
  the prediction, and only `DualCapture` publishes one. Without it `/score/iou`
  never publishes, the panel's score stays empty and the director reports
  `no scores received` and `n=0`. Video works fine without it — panes 1–3 are
  independent of scoring.
- **A second rosbridge.** `rosbridge_websocket` does not exit when 9090 is
  taken; it logs `[Errno 98] Address already in use` and retries every two
  seconds forever. The panel then connects to the *older* instance from a
  previous run and shows a mix of live and stale data. `./rig mock` and
  `./rig sweep` now detect this and skip starting a second one.
- **Both topics flowing, still no score.** `ApproximateTimeSynchronizer` matches
  on header stamps, not arrival time. Two publishers on different clocks — one
  stamping `now()`, one republishing a source stamp, one leaving it at zero —
  produce a healthy stream on both topics and not one pair. `scoring_node` now
  says so explicitly, with counts for each side.

Every node reports a silent input on a timer now, naming the topic and listing
what is actually published (`topicwatch.py`), and the panel asks rosapi the same
question and writes the answer into the empty pane.

### Running without ground truth

Video and scoring are independent. With a camera topic but no semantic-label
topic, panes 1–3 work normally and **no IoU is possible** — so the rig says that
rather than implying a number:

- `scoring_node` publishes the reason on `/score/detail`
  (`{"unavailable": true, "reason": "no ground truth: nothing on /unity/labels …"}`)
  and publishes **nothing** on `/score/iou`. A placeholder score is a number
  somebody eventually quotes.
- The panel's score card shows `n/a` and the reason, not a blank number.
- The director logs `NO SCORE (n=0)` instead of `IoU nan (drop +nan%)`, and
  `results.md` opens with **NO SCORES IN THIS RUN** instead of a table of `nan`
  with an arbitrary "worst condition" — `min()` over NaN returns whatever came
  first, because NaN compares False against everything.
- `results.csv` leaves unmeasured cells **empty**. NaN in a cell reads as a
  number that went wrong; empty reads as not measured.

One bug worth naming, since it made the panel look dead: `json.dumps` writes a
bare `NaN`, which is **not valid JSON**. The director's status message carried
`last_iou: NaN` the moment a condition went unscored, `JSON.parse` rejected the
whole message, and the panel's director table silently froze on its last good
value. NaN never crosses that boundary now, and a payload that will not parse
says so in the status bar instead of being swallowed.

## Running it with Unity

Do these in order. Each step is checkable, and a later step means nothing if an
earlier one is wrong.

**1. Start the ROS side of the Unity bridge.** Leave it running.

```bash
./rig endpoint
```

**2. In Unity:** add the ROS-TCP-Connector package, set *Robotics > ROS Settings*
to **ROS2** with ROS IP `127.0.0.1`, copy in `unity/Assets/MarsRig/`, then
`Mars Rig > Build Scored Scene`, and press **Play**. Full detail in
`unity/README.md`.

**3. Confirm frames actually arrive — from the ROS side, never from Unity.**

```bash
./rig topics                       # names, publishers, ports -- start here

source install/setup.bash
ros2 topic hz /unity/camera/image_raw               # expect ~24
ros2 topic hz /unity/labels                         # expect ~10
```

If `./rig topics` shows no publisher for these, Unity is publishing under
different names — pass them through rather than editing anything:
`./rig mock unity_image_topic:=... labels_topic:=...`

A publisher reporting 24 Hz while the subscriber sees 3 Hz is the failure mode
here, and it logs nothing. That is why this is checked from ROS.

**4. Run the alignment check** in Unity: `Mars Rig > Check Camera Alignment`.
Read `unity/README.md` for what its verdict means. If it fails, stop — every
number below it is wrong.

**5. Measure the ceiling.** Perception on the plain Unity render, scored against
Unity's own labels, with no world model in between:

```bash
./rig ceiling
ros2 topic echo /score/detail --once
```

Whatever that reports bounds every result you will quote. If it is low,
perception or label alignment is broken, not X2.

**6. Full loop, mock bridge — no credits.** The bridge tints Unity's frames
instead of calling X2, so the whole graph runs for free:

```bash
./rig mock settle_sec:=2.0 dwell_sec:=3.0     # short, just to watch it work
./rig panel                                    # in another terminal
```

All three panes should be live: Unity's render, the tinted "repaint", and the
segmentation. The score should move when the picture changes.

**7. The live run.** Only once 1–6 are all good:

```bash
export REACTOR_API_KEY=rk_...
./rig sweep
```

It states the one-key one-session constraint, asks before spending anything, and
closes the session on exit and on Ctrl-C.

### Topic wiring, end to end

Every hop is raw `sensor_msgs/Image`, and the names line up by convention rather
than by luck:

```
Unity DualCapture   ->  /unity/camera/image_raw              Image rgb8
                    ->  /unity/labels                        Image rgb8
                    ->  /unity/rover_pose                    PoseStamped

x2_bridge           <-  /unity/camera/image_raw              (input_compressed:=false)
                    ->  /world_model/image_raw               Image rgb8
                    ->  /world_model/camera_info             CameraInfo
                    ->  /world_model/state                   String (JSON)

perception          <-  /world_model/image_raw
                    ->  /perception/mask                     Image rgb8

scoring             <-  /perception/mask  +  /unity/labels
                    ->  /score/iou  +  /score/detail
```

Optional, and off by default — every one of these is a *pair* of settings, and
turning on only one half is silent:

```
DualCapture rgbAsJpeg     ->  /unity/camera/image_raw/compressed   jpeg
                              needs x2_bridge input_compressed:=true
DualCapture labelAsPng    ->  /unity/labels/compressed             png
                              needs scoring_node ground_truth_compressed:=true
x2_bridge publish_compressed:=true
                          ->  /world_model/image_raw/compressed    jpeg
perception publish_compressed:=true
                          ->  /perception/mask/compressed          png
```

BUILD_SPEC 5.2 asks for CompressedImage between processes; this deviates,
because the compressed topics were not actually being published and every
consumer that asked for one saw nothing. The bandwidth argument in 5.2 still
holds — raw rgb8 at 832×512×24 fps is ~30 MiB/s — so if the link cannot carry
it, drop `rgbFps` or the capture resolution in `DualCapture`, or turn the
compressed pair on at both ends.

**Image QoS.** Every image publisher here offers **RELIABLE** and every image
subscriber requests **BEST_EFFORT** (`image_pub_qos` / `image_sub_qos` in
`imgcodec.py`). This is not a detail: a BEST_EFFORT publisher and a RELIABLE
subscriber are *incompatible* in ROS 2 and never connect, and both
`rosbridge_server` and `web_video_server` subscribe RELIABLE. With the
sensor-data preset on the publishers, `ros2 topic hz` reports a healthy 24 Hz,
no node logs anything, and the panel is blank.

### "requested model is not available to this API key"

```
[x2_bridge] reactor loop died: could not exchange the API key for a token:
403 Forbidden -- {"error":"requested model is not available to this API key"}
```

The key authenticated; it is just not entitled to the model named `x2`. No
session opened, so **nothing was billed**. Check which models the key can use,
then pass the exact identifier:

```bash
ros2 launch mars_rig stress_rig.launch.py mock:=false model:=<name> ...
```

`model` defaults to `x2`. It is a launch argument rather than a code constant
because this is an entitlement problem, and the fix is a different string.

### The ceiling comes first

Score the plain Unity render against its own labels. That number is the
**ceiling** — every world-model result is measured below it. If it is low,
perception or label alignment is broken and no result below it means anything.
`./rig ceiling` exists to make this one command — it uses the real Unity feed
when ROS is built, and the synthetic scene when it is not.

---

## What is verified, and what is not

| | |
|---|---|
| IoU maths | tested against hand-worked numbers |
| Director sweep, adaptive, settle discard | tested on a simulated clock |
| Full loop closes end to end | run offline, 23 conditions |
| `publish_track` / `push_frame` call shapes | read from reactor-sdk 1.1.1 source and `help()` |
| Unity C# | compiles clean against stubs verified from ROS-TCP-Connector source |
| `./rig` offline path | run here: install, doctor, test, ceiling, offline, panel |
| Control panel | driven in Chromium against a fake rosbridge: all three panes decode raw `Image` and draw, live score, director and state tables, prompt publish, no JS errors |
| Panel raw decode | `rgb8`, `bgr8` (channel order), `mono8`, and a padded `step` checked pixel-for-pixel in the browser; an unknown encoding and a short payload both say so in the pane instead of drawing garbage |
| Image QoS pairing | reasoned from the ROS 2 compatibility rules, **not** run against a live rosbridge — no ROS in this container |
| `./rig topics` | run against a stubbed `ros2` reproducing the `/panther/*` graph: it names the missing topics and suggests `/panther/camera_left/image_raw`; port detection checked against a real listening socket |
| Blank-pane diagnosis in the panel | driven in Chromium: "nothing publishes `<topic>`" with the candidate list, "N publisher(s), no frames" and the healthy path, all three verified |
| `topicwatch.py` warnings | **not** run — needs rclpy, which this container has no ROS for. Read the logic before trusting the wording |
| Unscored-run reporting | unit-tested end to end: `NO SCORES IN THIS RUN` banner, no `nan` anywhere in `results.md`, no "worst condition", empty CSV cells, and a status payload that survives `JSON.parse` |
| Panel score card | driven in Chromium: live score, `n/a` + reason on unavailable, recovery to a real score, malformed payload reported rather than swallowed |
| MJPEG stream URLs | asserted in Chromium to contain no `%2F` — the encoding that made `web_video_server` reject every topic |
| `tools/panel_server.py` | `Cache-Control: no-store` on every response, and a conditional request with a future `If-Modified-Since` answered `200`, not `304` |
| Page freshness self-check | driven in Chromium both ways: a current page reports `current` and does not navigate, a page told it is stale reloads through `?v=<mtime>` and then settles — one navigation, no loop |
| Node teardown on Ctrl-C | reasoned from the rclpy shutdown sequence, **not** run here (no ROS in this container) |
| `./rig` ROS path | **not run** — no ROS in the container it was written in |
| Bridge against a **live** X2 session | **not yet run** |
| Unity scripts in the actual editor | **never opened** — expect to fix something |
| Camera alignment (BUILD_SPEC 5.1) | **not yet measured** — run `Mars Rig > Check Camera Alignment` |
| Any IoU number as a measurement | **no** — needs Unity and a live session |

---

## Reactor SDK facts (verified against `reactor-sdk` 1.1.1)

These resolve BUILD_SPEC 9.1 and override anything older.

```python
src = await reactor.publish_track("source")   # a NAME, never a track object
src.push_frame(rgb)                           # SYNCHRONOUS, (h, w, 3) uint8 RGB

out = reactor.track("main_video")             # bind by name, BEFORE connect()
@out.on_frame
def on_frame(frame):                          # RGB (h, w, 3) array
    ...
```

- `publish_track(name: str) -> Track`. Passing a track object raises
  `AttributeError` on `name.encode()`.
- **aiortc and `av` are not needed.** `reactor-sdk` declares no runtime
  dependencies (numpy optional).
- `push_frame` does no pacing — the caller owns the 24 fps clock.
- `reactor.tracks` is **empty until capabilities arrive** shortly after
  `connect()`, so `tracks.with_direction(...).one()` races the connect. Bind by
  name instead.
- Timestamps, if ever needed, come from `reactor_sdk.time_micros()` — not
  `time.time()`.
- There is no `start` command. Generation begins once a non-empty prompt is set
  **and** source frames are flowing. Both.

---

## Things that will silently produce garbage

Each of these fails with no error anywhere, which is why they are listed.

- **Scoring perception against itself.** The scorer must subscribe to Unity's
  label camera. A scorer that locks a previous *prediction* as its reference
  measures temporal self-consistency, not accuracy, and reports 1.000 for a
  perception stack that has failed completely.
- **Returning 1.0 when nothing is detected.** Total failure must score 0 or be
  skipped, never 1.
- **Unsynchronised frames.** Pair them with `ApproximateTimeSynchronizer`, or
  you score one frame's prediction against another frame's labels and still get
  a plausible-looking number.
- **JPEG on a label image.** Lossy compression bleeds colour across class
  boundaries. Labels go raw or PNG. (The scorer quantises to the nearest
  palette colour, which tolerates drift — it does not undo JPEG.)
- **Interpolating class ids on resize.** Nearest-neighbour only; bilinear
  invents classes that exist in neither image.
- **Raw RGB at 24 fps over TCP.** ~24 MiB/s. The publisher reports 24 Hz, the
  subscriber sees 3 Hz, and nothing logs an error. Lower the capture rate or
  resolution, or turn on a compressed transport — *at both ends*.
- **A compressed topic wired at one end only.** `<topic>/compressed` is a
  different topic, not a view of `<topic>`. Subscribe to it when nothing
  publishes it and you connect to nothing: no frames, no error, an empty panel
  pane, and `ros2 topic hz` on the publisher still says 24. This applies to
  `web_video_server`'s `default_transport` exactly as much as to a node
  parameter.
- **A topic name nobody publishes.** The single most expensive failure here.
  The subscription is silent — no error, no warning, not even at debug — so the
  node looks healthy and the symptom surfaces three hops away as a blank pane or
  a missing score. `./rig topics` answers it in one command, and every node now
  warns on a timer with the names that *are* published.
- **A percent-encoded topic in a web_video_server URL.** `%2F` is not a `/`;
  rclcpp rejects the name and the stream dies. The message goes to
  `web_video_server`'s stdout only, so the browser shows a black pane with a
  clean console and nothing in the panel says a word.
- **A BEST_EFFORT image publisher.** `rosbridge_server` and `web_video_server`
  subscribe RELIABLE, and RELIABLE-subscriber-to-BEST_EFFORT-publisher is an
  incompatible QoS pair: the two never connect and rclpy says nothing at default
  severity. Publish images RELIABLE and subscribe BEST_EFFORT — a publisher may
  always offer more than a subscriber asks for. See `image_pub_qos` /
  `image_sub_qos` in `imgcodec.py`.
- **Naming a protected object in a prompt.** Naming the rover once turned every
  ArUco marker in the scene into a robot. Protect by count and stillness.
- **One log call site, two severities.** rclpy keys its logging context on the
  call site and refuses a severity change under it, so
  `getattr(logger, level)(msg)` or `lvl = logger.info if ok else logger.warn`
  raises `ValueError: Logger severity cannot be changed between calls.` from
  inside the logging call and kills the node. It needs two different severities
  through one line to show up, so it survives every short test and fires later.
  Use `roslog.emit()`, which gives each severity its own line.
- **Two OpenCVs on one interpreter.** A pip-installed OpenCV 5 (`ultralytics`
  pulls one in) shadows the system OpenCV that ROS was built against. OpenCV 5
  renumbered the type constants — `CV_8UC3` is `64` there and `16` in OpenCV 4 —
  so `cv_bridge` resolves an encoding through its compiled module and looks the
  answer up in a table built from the *other* OpenCV, and dies with
  `KeyError: 16` from inside a publish call. Nothing in that traceback says
  "you have two OpenCVs". `mars_rig` does not use `cv_bridge` for this reason
  (see `imgcodec.py`); `./rig doctor` reports a shadowing OpenCV anyway, because
  other ROS python packages still have the problem.

---

## Open decisions

Resolved: **9.1** (SDK call shapes, above), **9.3** (ROS 2 Jazzy; Unity and ROS
on the same box).

Still open — see BUILD_SPEC section 9:

- **9.2 perception model.** `perception_node` runs `baseline_cv`, a classical
  stand-in, so the loop closes today. Note that an off-the-shelf COCO model
  (e.g. YOLOv8n-seg) does not drop in: its classes are person/car/etc. and have
  no relationship to Unity's drivable/obstacle labels, so the mask cannot be
  scored without a remap or a retrain.
- **9.4 metric scope.** Traversability IoU only. No depth metric built.
- **9.5 images to the browser.** The panel implements **both** `web_video_server`
  and raw-Image-over-rosbridge, switchable at runtime, so this can be decided by
  trying it rather than in advance. Both read the raw topics.
- **9.6 prompt language.** Catalog has EN and ZH for all 45; `lang` defaults to
  `zh`. Not A/B tested — that costs credits.
- **9.7 keep_backlog.** `true` for scoring runs, `false` for the live demo.
  Launch argument, defaults to `true`.

Which classes count toward the headline mean IoU is a `scored_classes`
parameter, defaulting to `drivable` and `obstacle`. `sky` is in the palette but
excluded: it is large and trivially separable, so including it inflates the mean
and hides the ground/obstacle degradation the rig exists to measure.

## Read the obstacle column, not the mean

`/score/iou` is mean IoU over `drivable` and `obstacle`, and it **cannot fall
much below 0.45**. Drivable is trivially easy — a segmenter that gives up and
calls the whole frame ground is still right about the ground — so it holds the
mean up no matter how completely perception stops seeing rocks.

A measured example from a sweep: under `dust_storm`, obstacle IoU was `0.000`
and the headline mean read `0.484`. Perception found *no rocks at all*, which
for a rover means driving into every one of them, and the mean called it
"degraded".

So `results.md` and `results.csv` carry a per-class column, and the summary
names every condition where obstacle IoU hit the floor. Two consequences worth
knowing:

- `break_below` defaults to **0.50**, not 0.25. At 0.25 adaptive mode could
  never fire on the most likely failure — it would walk the whole ladder and
  report "never broke" while perception was obstacle-blind throughout.
- When you quote a single number, quote obstacle IoU alongside the mean. The
  mean alone understates every failure this rig is built to find.
