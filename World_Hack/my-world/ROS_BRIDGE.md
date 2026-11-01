# Stream mode - Unity into X2, nothing in between

The Python path to X2 does not work. `x2_bridge_node.py` sends the raw API key
and gets:

```
403 Forbidden - {"error":"requested model is not available to this API key"}
```

The JS SDK authenticates differently: the key stays server-side
(`app/api/reactor/token`), a JWT is minted, and the browser connects over
WebRTC. No model name fixes the Python path - it is the wrong flow.

## The pipeline

```
Unity ─→ ros_tcp_endpoint ─→ /unity/camera/image_raw
                                      │
                            web_video_server :8080
                                      │ MJPEG over HTTP
                                /api/wvs (this app, same-origin)
                                      │ fetch + multipart reader
                                  <canvas>
                                      │ captureStream(24)
                                  `source` ──WebRTC──→ X2
                                                        │ main_video
                                             <X2MainVideoView />
```

**No ROS client in the browser.** `web_video_server` turns any image topic into
MJPEG, so the app speaks plain HTTP. No rosbridge, no websocket, no topic
lists. You type prompts in the app.

## What you run

```bash
./rig endpoint                                  # then press Play in Unity
ros2 run web_video_server web_video_server      # that is the whole ROS side
cd World_Hack/my-world && npm run dev
```

Then **Source → Stream → Start → Connect**, and set a prompt. X2 has no start
command: generation begins on its own once a non-empty prompt is set **and**
source frames are arriving.

The URL field takes any "Stream" link from the index at
**http://localhost:8080** - paste one and it becomes the model's input.

## Putting the rig back in the loop

The checkbox **publish back to ROS** under the Source panel turns the return
leg on: `main_video` → `/world_model/image_raw`, and `/world_model/prompt` →
the model, so `perception_node`, `scoring_node` and the control panel work as
they did with `x2_bridge_node.py`. It owns the rosbridge connection, so with it
off the page opens no websocket at all.

With it on, launch the rig with **`bridge:=false`** - `x2_bridge_node` publishes
`/world_model/image_raw` too, and two publishers on one topic is not a fallback:
perception segments whichever frame arrived last and nothing reports a conflict.

## Three things that each cost hours

**Never send images over rosbridge.** A 1920×1080 rgb8 frame is ~6 MiB and
rosbridge base64s `uint8[]` into JSON, so it crosses at ~8 MiB. At 24 fps that
is ~200 MiB/s asked of a single-threaded Python server: it does not drop frames,
it drowns. The subscription stays live, the topic keeps its publisher, and
nothing arrives - indistinguishable from a wrong topic name. MJPEG of the same
feed is ~50–100 KiB a frame.

**The proxy is a route handler, not a `next.config` rewrite.** `next dev`
buffers a rewrite - it waits for the body to end, and `multipart/x-mixed-replace`
never ends. Measured against the same server in the same four seconds:

```
direct to web_video_server   2,636,720 bytes
through the dev rewrite              0 bytes
through the route handler    3,337,170 bytes
```

It worked under `next start` and failed for everyone running `npm run dev`.

**Never percent-encode the topic.** `web_video_server` does not URL-decode its
query parameters, so `encodeURIComponent` turns `/perception/mask` into
`%2Fperception%2Fmask`, which reaches rclcpp as a literal topic name and is
rejected. The error appears only in that node's stdout.

## The files

| | |
|---|---|
| `app/lib/ros/mjpeg.js` | fetch + multipart reader → canvas → track |
| `app/components/StreamSource.tsx` | the Stream source mode |
| `app/api/wvs/[...path]/route.ts` | streaming proxy to web_video_server |
| `app/components/useRosOutput.ts` | the optional return leg (off by default) |
| `app/lib/ros/{rosbridge,bridge}.js` | rosbridge protocol + `X2Sink`, used only by the return leg |

## What is tested

| | |
|---|---|
| MJPEG in, under `next dev` | 1920×1080 stand-in: `HTTP 200 · 84 frames · 3.1 MB · 1920×1080`, canvas `getImageData` succeeds - so it is **not tainted** and can be captured |
| the proxy | streams in dev (0 bytes before, 3.3 MB after) and passes `Content-Type` through so the multipart boundary survives |
| **the track X2 receives** | pushed through a real `RTCPeerConnection`: `connected`, **85 frames sent, 85 decoded** on the far side, 93 KB |
| return leg (when enabled) | 30 frames published as `rgb8` `Image`, `step == width*3`, payload exactly `width*height*3` bytes |
| prompt forwarding | trims, verbatim, ignores empty, caps at 1000 |
| **a live X2 session** | **never run** - no API key in the environment this was written in |

One thing the loopback showed: WebRTC downscaled 1920×1080 to **640×360** for
transport. That is the browser encoder adapting, and it is normal - X2 picks its
own output resolution anyway. If the model wants more detail, lower the capture
resolution in Unity rather than fighting the encoder.
