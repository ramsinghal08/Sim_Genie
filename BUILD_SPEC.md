# BUILD SPEC — Martian Perception Stress-Test Rig

**Read this entire file before writing any code.**

---

## 0. HOW TO WORK WITH ME

I am a member of the Software Subsystem, Mars Rover Team, IIT Bombay. We are
mid-hackathon with a hard deadline. I need you to move fast **but not to guess**.

### Ask me before deciding

Stop and ask when you hit any of these. Do not pick one and proceed.

- Anything in **§9 DECISION POINTS** — those are already-known open questions
- A library, model, or framework choice not already named in this spec
- Any change to a ROS topic name, message type, or the JSON shape of a topic
- Any change to the scoring metric or how it is computed
- Any change to prompt text in the catalog (a single wrong word invalidates results — see §5.3)
- Anything that spends API credits beyond what the task needs
- Restructuring directories or renaming existing files
- When something in this spec contradicts what you find in the code or docs

When you ask, give me: what you found, 2–3 concrete options, your recommendation
and why, and what you will do if I do not reply in 5 minutes. Then wait.

### Do NOT ask, just do

- Bug fixes, type errors, import errors, obvious typos
- Adding logging, error handling, docstrings
- Writing tests
- Anything explicitly specified below

### Working style

- Build the **smallest thing that runs end to end first**, then improve it.
  A partial pipeline that runs beats a perfect component that is not wired in.
- Every node must work in `mock:=true` mode with no API key. Four people are
  working in parallel against ONE paid session.
- Commit after each working component. Small commits.
- If you are blocked for more than 10 minutes, stop and tell me.
- Never leave a Reactor session open. Credits bill per second.

---

## 1. WHAT WE ARE BUILDING AND WHY

### One line

We can't take our rover to Mars, so we bring Mars to the rover — and then make
it worse on purpose, until perception breaks.

### The problem

Rover autonomy is validated on terrain we can reach, not terrain we will face.
Simulators give exact ground truth but look synthetic, so perception that passes
in Gazebo fails on real rock. Real imagery looks right but has no ground truth
and only covers conditions we happened to encounter. Neither can manufacture the
rare cases that break a rover: low sun on loose regolith, dust washing out
contrast, a shadowed crater rim.

### The solution

Combine both halves.

- **Unity** owns physics, collision, and ground truth. It knows where every
  rock is because it placed them.
- **Reactor X2** (a live conditional video model) repaints Unity's frames into
  photorealistic Mars. It changes appearance, not geometry.
- **Our ROS 2 perception stack** runs on the repainted view, unmodified.
- **A scorer** compares perception's output against Unity's ground truth.
- **A director** escalates conditions until perception breaks.

### The deliverable

NOT a better rover. A **reproducible catalogue of the conditions under which
our perception fails**, with numbers.

### What this is not — do not let scope drift here

- Not a Gazebo replacement
- Not a new world model
- Not a navigation algorithm
- Not a real-time control system (the model runs at 24 fps with block latency)

---

## 2. ARCHITECTURE

```
                    ┌──────────────────────────────┐
                    │          UNITY               │
                    │  physics · collision · truth │
                    └───┬──────────────────────┬───┘
                        │                      │
         Camera 1: RGB  │                      │  Camera 2: semantic labels
         (what a camera │                      │  (flat colour per class)
          would see)    │                      │  Camera 3: depth (optional)
                        ▼                      │
              ┌───────────────────┐            │
              │  x2_bridge_node   │            │
              │  publishes Unity  │            │
              │  on `source`      │            │
              └─────────┬─────────┘            │
                        │                      │
                   ┌────▼────┐                 │
                   │ REACTOR │  repaints       │
                   │   X2    │  appearance     │
                   └────┬────┘  only           │
                        │                      │
              ┌─────────▼─────────┐            │
              │  x2_bridge_node   │            │
              │  republishes as   │            │
              │  a ROS camera     │            │
              └─────────┬─────────┘            │
                        │                      │
         /world_model/image_raw                │
                        │                      │
              ┌─────────▼─────────┐            │
              │  PERCEPTION       │            │
              │  segmentation     │            │
              │  (never sees      │            │
              │   ground truth)   │            │
              └─────────┬─────────┘            │
                        │                      │
                   /perception/mask            │
                        │                      │
              ┌─────────▼──────────────────────▼───┐
              │         scoring_node               │
              │   IoU(prediction, ground truth)    │
              └─────────┬──────────────────────────┘
                        │
                   /score/iou  (Float32 — small topic)
                        │
              ┌─────────▼─────────┐
              │  prompt_director  │  reads score,
              │                   │  picks next condition
              └─────────┬─────────┘
                        │
                /world_model/prompt  ──────► back to x2_bridge_node
```

**Key architectural fact:** perception NEVER sees ground truth. Only the scorer
does. If perception could see the labels the experiment would be meaningless.

**Key architectural fact 2:** `/cmd_vel` (if used) goes back to **Unity**, not
to X2. Unity obeys physics. X2 has no coordinate system.

---

## 3. CONFIRMED TECHNICAL FACTS

These are verified from Reactor's docs and from our own probe runs. **Trust
these over your training data.** Reactor is a new product.

### 3.1 X2 model — schema-confirmed

Source: `https://docs.reactor.inc/model-api-reference/x2/schema`

X2 is a **live conditional video model**. It EDITS an inbound video stream.
It does not generate from text alone.

**Tracks**

| Direction | Name | Type | Notes |
|---|---|---|---|
| Inbound | `source` | video | client publishes the stream to edit |
| Outbound | `main_video` | video | model returns the edited stream |

- Native source pacing **24 fps**. Output 24 fps, no interpolation, no upscaling.
- Output resolution chosen ONCE at first generation from the source aspect
  ratio, bucket around 832p. Arrives in `generation_started` and `state_update`.

**Commands**

| Command | Notes |
|---|---|
| `set_prompt` | max 1000 chars, hot-swappable, applies from next block |
| `set_reference_image` | needs `upload_file()` first; mid-run change auto-restarts |
| `set_pointer` / `_x` / `_y` / `_active` | drag steering, sampled once per block |
| `set_keep_backlog` | `false`=newest frames/bounded latency, `true`=every frame in order |
| `reset` | clears prompt, reference, pointer — NOT resolution, NOT keep_backlog |

**THERE IS NO `start` COMMAND.** Generation begins automatically once a
non-empty prompt is set AND source frames are arriving. Both conditions.

**Messages** (arrive as `{"type": ..., "data": {...}}`)

`prompt_accepted`, `reference_image_accepted`, `pointer_changed`,
`generation_started`, `generation_stopped`, `command_error`, `state_update`.

`generation_stopped` with reason `reference_image_changed` is an automatic
restart, not an end state. Only reason `reset` means truly stopped.

`state_update` is the single source of truth: `prompt`, `has_reference_image`,
`pointer_x/y/active`, `keep_backlog`, `generating`, `width`, `height`.

**Block semantics:** the model generates one block at a time. Prompt changes
apply from the next block. This is why the director has a settle period.

### 3.2 Pricing (from `https://api.reactor.inc/pricing`)

10000 credits = $1. Rates per second:

| Model | credits/sec | $/hr | Access |
|---|---|---|---|
| `x2` | 17 | ~$6 | ✅ works |
| `helios` | 17 | ~$6 | ✅ works |
| `sana-streaming` | 17 | ~$6 | untested |
| `longlive-v2` | 17 | ~$6 | untested |
| `lingbot` / `lingbot-world-2` | 33 | ~$12 | untested |
| `happy-oyster*` | 139 | ~$50 | ❌ 403 on our key |
| `ltx2` | 300 | ~$108 | ❌ 403 on our key |

### 3.3 SDK reality (beta — docs and package disagree)

`Reactor` methods actually present in the installed package:

```
close, connect, disconnect, download, download_clip, download_recording,
get_session_id, get_status, off, on, on_error, on_message, on_status,
on_track, paused_tracks, publish_track, reconnect, request_clip,
request_recording, request_schema, session_id, status, track, tracks,
unpublish_track, upload_file
```

- **`get_capabilities()` does NOT exist.** Use `request_schema` instead.
- `publish_track` exists but **its signature is unverified** — see §9.1.
- `request_recording` / `download_recording` can capture the session
  server-side. This is probably how we get clean demo footage. Untested.

### 3.4 Our own measurements

**Helios drift** (measured, 45s probe, 950 frames at 14.4 fps): edge agreement
0.335 fresh → flat ~6s → decays → step down at ~15.5s → collapsed by 45s, with
hallucinated buildings and vehicles appearing.

**This does not apply to X2.** X2 consumes the source every frame, so there is
no seed decay and no re-anchor requirement. If you find drift in X2, tell me —
that would be a significant and unexpected finding.

**X2 structure preservation** looks good on a real Mars photo (rocks held
position while dust and lighting changed). NOT yet verified on a Unity render.

### 3.5 The marker incident — an expensive lesson

We prompted X2 with a Chinese instruction that named 探测车 (rover) as a thing
to preserve. Result: **every ArUco marker in the scene turned into a robot, and
hundreds of robots started moving around.**

Fix: never name a protected object by category. Naming it teaches the model to
look for it and invent more. Protect by **count** and **stillness** instead.

The current boundary in every catalog prompt:

```
只改变地面和天空的材质与颜色。画面中所有非地面物体完全静止，不产生任何自身运动。
保留所有物体的形状、结构、数量和位置，保留画面构图、地平线和镜头运动。
```

> Only ground and sky material and colour change. All non-ground objects are
> completely motionless and generate no self-motion. Preserve the shape,
> structure, count and position of every object, the composition, the horizon
> line, and the camera motion.

---

## 4. EXISTING ASSETS

Already written and syntax-checked. **Read each file before modifying it.**

| File | Status | Notes |
|---|---|---|
| `x2_bridge_node.py` | written, UNTESTED against live X2 | the core bridge |
| `prompt_director.py` | written, UNTESTED | sweep/adaptive/llm modes |
| `prompt_catalog_x2.json` | done | 45 prompts, 8 families, EN+ZH |
| `model_probe.py` | working | one-shot model evaluation |
| `compare_overlay.py` | working | edge-agreement structure check |
| `reactor_bridge_node.py` | **DEAD — ignore** | Helios-era, wrong model |

Delete `reactor_bridge_node.py` and `sana_restyle_prompts.md` if present; both
are superseded.

---

## 5. COMPONENTS TO BUILD

Ordered by criticality. **Build the walking skeleton across all of these before
polishing any one of them.**

### 5.1 Unity side — HIGHEST RISK, LONGEST POLE

Not your task if a teammate owns it — **ask me who owns Unity before touching
it.** If it is your task:

- Mars-ish terrain: ground plane, scattered rocks, a slope, a crater lip
- **REMOVE the ArUco markers and the traffic cone** from the scored scene.
  Keep a separate `calibration_scene` WITH them for one structure measurement.
- Fix the black band at the horizon (far clip plane / terrain edge)
- Disable motion blur in post-processing
- Camera tilted down so ground fills ~75% of frame
- Rover drives a **scripted fixed path** — identical every run. Not teleop.
  Identical motion is what makes score changes attributable to visuals alone.
- **Camera 1**: RGB, normal render → this is the X2 source
- **Camera 2**: same transform, replacement shader, flat colour per class
  (drivable / obstacle, at minimum) → this is the answer key
- **Camera 3**: depth buffer (optional, only if the depth metric is in scope)

**CRITICAL CHECK:** Camera 1 and Camera 2 must be pixel-identical — same
transform, FOV, resolution, and frame. Verify by overlaying two screenshots and
confirming rocks land in the same place. **If this fails, every number
downstream is wrong.** Do this before anything else and show me the result.

### 5.2 Unity ↔ ROS 2

- ROS-TCP-Connector (Unity Robotics Hub) + `ros_tcp_endpoint` node
- Publish `/unity/camera/image_raw` — **CompressedImage**, not raw. Raw RGB at
  24 fps is ~24 MiB/s and will choke TCP and DDS. This will manifest as the
  publisher reporting 24 Hz while the subscriber sees 3 Hz, with no error
  anywhere.
- Publish `/unity/labels` (semantic), `/unity/depth` (if in scope),
  `/unity/rover_pose`
- Subscribe `/cmd_vel` if the scripted path is driven from ROS
- Verify with `ros2 topic hz` from the ROS side, not the Unity side

### 5.3 `x2_bridge_node.py` — exists, needs live verification

Already handles: Unity frame in → `source` track, `main_video` out → ROS,
`/world_model/prompt` → `set_prompt`, mock mode, compressed output, state
republishing.

Your job:
- Verify `publish_track` actually works (see §9.1)
- Verify frames flow both directions
- Confirm `set_keep_backlog` behaves as documented

**Do not change prompt text passing through this node.** It forwards verbatim.

### 5.4 Perception node

- Subscribes `/world_model/image_raw`
- Publishes `/perception/mask` — same size as input, same class encoding as
  Unity's label camera
- **ASK ME (§9.2)** whether to use our MRT stack or an off-the-shelf model

### 5.5 `scoring_node.py` — BUILD THIS, it does not exist yet

The most important component that has not been written.

```
Subscribes:
    /perception/mask     sensor_msgs/Image   prediction
    /unity/labels        sensor_msgs/Image   ground truth
Publishes:
    /score/iou           std_msgs/Float32    mean IoU, THIS drives the director
    /score/detail        std_msgs/String     JSON: per-class IoU, pixel counts
```

Requirements:

- Use `message_filters.ApproximateTimeSynchronizer` to pair frames. Unsynced
  comparison silently produces garbage.
- Resize prediction to ground-truth resolution if they differ. X2 picks its own
  output resolution (~832p bucket) which will NOT match Unity's render size.
  **Log a warning the first time you resize** so we know it is happening.
- Compute IoU per class, then mean. Ignore any "unlabelled" class.
- Handle the empty-union case (class absent from both) — skip, do not count
  as 0 or 1, both are wrong.
- Publish at most 10 Hz even if frames arrive faster. The director averages
  these; it does not need every frame.
- **Add a `--fake-ramp` mode** that publishes a descending score with no
  inputs. This lets us test the director without perception or Unity running.

Sanity test before trusting any output: score the plain Unity render against
its own labels. That is the CEILING. If it is low, perception or label
alignment is broken and no world-model result means anything.

### 5.6 `prompt_director.py` — exists, needs testing

Three modes: `sweep` (walk all 23 ladder conditions, write results.csv +
results.md — **this produces our deliverable**), `adaptive` (escalate until
IoU < 0.25, the demo mode), `llm` (adaptive + local Ollama, with catalog
fallback on any error).

**Do not shorten `settle_sec`.** X2 applies prompts at block boundaries; scores
collected too early belong to the previous condition.

### 5.7 Control panel — BUILD THIS, it does not exist

Judging weights *Real Time* and *User Experience*. A results table alone loses
points on both.

Single HTML page, plain JS, rosbridge websocket. No framework. Target one hour.

- Three panes side by side: Unity render | X2 output | segmentation overlay
- Large live `/score/iou` readout
- A text box: type a condition, publish to `/world_model/prompt`, watch the
  terrain change and the score fall
- Director status from `/director/status`

**ASK ME (§9.5)** before choosing how images reach the browser.

### 5.8 MCP agent — OPTIONAL, CUT IF BEHIND

Only if everything above works. `robotmcp/ros-mcp-server` (needs rosbridge) or
`wise-vision/ros2_mcp` (stdio, must be co-located).

Claude reads `/score/iou`, publishes `/world_model/prompt`.

**NEVER pipe images through MCP.** One frame serialised to JSON is megabytes of
noise. Small numeric topics only.

---

## 6. REPO LAYOUT

Propose this and let me confirm before creating it:

```
mars_stress_rig/
├── mars_rig/                      # ROS 2 python package
│   ├── mars_rig/
│   │   ├── x2_bridge_node.py
│   │   ├── prompt_director.py
│   │   ├── scoring_node.py
│   │   ├── perception_node.py
│   │   └── fake_score_publisher.py
│   ├── resource/
│   ├── package.xml
│   ├── setup.py
│   └── config/
│       └── prompt_catalog_x2.json
├── tools/
│   ├── model_probe.py
│   └── compare_overlay.py
├── panel/
│   └── index.html
├── unity/                         # notes only, project lives elsewhere
├── results/                       # results.csv, results.md, bags
└── README.md
```

---

## 7. CONVENTIONS

- Python 3.10+, ROS 2 (ask me which distro — §9.3)
- Every node takes ROS parameters, no hardcoded paths
- Every node has a `mock` mode that runs with no API key
- Sensor topics use `QoSPresetProfiles.SENSOR_DATA`
- Images between processes: **CompressedImage**
- Log at `info` for state changes, `warn` for degraded operation,
  `error` for broken. No `print()`.
- `reactor-sdk` is asyncio, `rclpy` is not — run the async client on its own
  thread and hand frames across with a lock, as `x2_bridge_node.py` does
- Comments explain **why**, not what

---

## 8. TEST PLAN

Do these in order. Each isolates a different failure.

1. **Unity camera alignment** — overlay Camera 1 and Camera 2 screenshots.
   Rocks in the same place? If no, stop, nothing else matters.
2. **Scoring node alone** — two handmade PNG masks where you know the answer.
   Does IoU match hand calculation?
3. **Bridge in mock mode** — `ros2 topic hz /world_model/image_raw/compressed`
   at ~24 Hz. `ros2 topic bw` on raw vs compressed — record the ratio.
4. **Director + fake score** — `-p settle_sec:=2 -p dwell_sec:=3`, confirm
   `results.csv` and `results.md` appear and populate.
5. **Unity → ROS** — from a second machine or terminal, confirm the subscriber
   sees the same Hz the publisher reports.
6. **Full loop, mock** — a frame travels Unity → bridge → perception → score
   → director → prompt → bridge.
7. **Full loop, live X2** — then immediately `ros2 bag record` and
   **screen-record the demo**.
8. **Sweep run** — the real one, `settle_sec:=8 dwell_sec:=25`, ~13 minutes
   for 23 conditions.

---

## 9. DECISION POINTS — ASK ME

### 9.1 `publish_track` signature
Unverified. `x2_bridge_node.py` tries three call shapes. Run
`python3 -c "from reactor_sdk import Reactor; help(Reactor.publish_track)"`,
show me the output, and propose the fix. Do not guess.

### 9.2 Perception model
Our own MRT stack (better story: "our URC stack, tested on Mars") or
off-the-shelf (SegFormer-B0, DeepLabV3-MobileNetV3)? Depends on whether ours
is containerised and runnable tonight. **Ask me.**

### 9.3 Environment
Which ROS 2 distro, which machine runs what, is Unity on the same box as ROS?
This determines the MCP server choice and whether raw images are viable.

### 9.4 Metric scope
Traversability IoU is the confirmed headline. Depth RMSE as a second metric is
desirable but needs Unity's depth camera. **Ask before building depth.**

### 9.5 Images to the browser
`web_video_server`, base64 over rosbridge, or MJPEG? Affects latency and setup
time. **Ask me.**

### 9.6 Prompt language
Catalog has EN and ZH for all 45. Reactor's guide recommends Chinese for
consistency. We have NOT A/B tested this. Propose running B00 in both and
comparing IoU before committing — but **ask me** before spending the credits.

### 9.7 keep_backlog
`true` for scoring runs (every frame, reproducible), `false` for live demo
(bounded latency). If you think a run needs the other setting, **ask me**.

---

## 10. HARD CONSTRAINTS

- **Deadline: 16:00 Sunday** evaluation. Demos 18:00–19:00. Code freeze 14:30.
- **Deliverables:** working demo + 1–2 min walkthrough video + social post
  tagging @thelaunchd and @reactorworld, submitted via the WhatsApp group.
  Budget the last 90 minutes for these, not for code.
- **One API key, one live session.** `ConflictError` on a second concurrent
  session. Everyone else runs mock.
- **Credits bill per second.** Never leave a session idle.
- **Record the demo the moment it first works.** Not at 15:00.

---

## 11. DEFINITION OF DONE

Minimum viable — this is a complete project:

- [ ] Unity renders RGB + aligned labels, rover on a scripted path
- [ ] X2 repaints the RGB stream, ROS receives it
- [ ] Perception runs on it, scorer produces IoU
- [ ] Sweep produces `results.md` with a real table
- [ ] Demo video recorded

Strong:

- [ ] Control panel — judge types a condition, watches the score fall
- [ ] Baseline-vs-worst comparison with numbers

Stretch:

- [ ] Adaptive mode hunting for the failure point live
- [ ] MCP agent driving it

---

## 12. THINGS WE SAY OUT LOUD

Honesty about limits reads as rigour and pre-empts the questions judges ask.

- X2 repaints for appearance. Structure preservation is empirical, not
  guaranteed. We measured it.
- Our IoU inherits that noise.
- The generated world has no true depth, pose, or collision — those all come
  from Unity, which is exactly why Unity is in the architecture.
- **"Isn't this just Gazebo with a filter?"** Gazebo cannot make photorealistic
  dust, so it cannot tell you whether perception survives it. We measure that.
- Include at least one condition where the system fails completely. A rig that
  never breaks anything is not measuring anything.
