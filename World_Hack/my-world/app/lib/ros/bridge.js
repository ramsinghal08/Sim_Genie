/**
 * bridge.js -- Unity/ROS <-> Reactor X2, with the browser as the bridge.
 *
 *   /unity camera topic  --rosbridge-->  <canvas>  --captureStream-->  X2
 *   X2 main_video track  --> <video> --> <canvas> --rosbridge--> /world_model/image_raw
 *
 * WHY THE BROWSER
 * ---------------
 * The Reactor JS SDK authenticates with a JWT minted server-side and moves
 * video over WebRTC. `publishTrack(name, track)` takes any MediaStreamTrack,
 * and a canvas produces one via captureStream() -- so there is no need for a
 * webcam, a virtual camera device, or a v4l2 loopback. We draw ROS frames into
 * a canvas and publish the canvas.
 *
 * Nothing downstream changes. perception_node, scoring_node, prompt_director
 * and the control panel only ever spoke ROS topics, and they still do.
 *
 * SDK-AGNOSTIC ON PURPOSE
 * -----------------------
 * This file never imports the SDK. It is handed a small adapter:
 *
 *     { publishTrack(name, track), sendCommand(cmd, data), onOutputTrack(fn) }
 *
 * so the ROS half can be tested headlessly against a stub, which is the only
 * half that can be tested without an API key and a live session.
 */

import { Rosbridge, imageToImageData, imageDataToMsg } from "./rosbridge.js";

/** Wall clock -> builtin_interfaces/Time. */
function nowStamp() {
  const ms = Date.now();
  return { sec: Math.floor(ms / 1000), nanosec: (ms % 1000) * 1e6 };
}

/**
 * ROS camera topic -> a MediaStreamTrack X2 can consume.
 *
 * The canvas is sized to the first frame that arrives, so it matches whatever
 * Unity is actually publishing rather than a guess. captureStream(fps) then
 * samples it: the canvas is repainted on every ROS frame and sampled on the
 * stream's own clock, so a slow camera repeats frames rather than stalling the
 * track -- which matters, because X2 stops generating without a source.
 */
export class UnitySource {
  constructor(ros, topic, { fps = 24, throttleMs = 0 } = {}) {
    this.ros = ros;
    this.topic = topic;
    this.fps = fps;
    this.throttleMs = throttleMs;
    this.canvas = document.createElement("canvas");
    this.ctx = this.canvas.getContext("2d", { willReadFrequently: false });
    this.frames = 0;
    this.lastStamp = null;
    /** @type {(frames: number, width: number, height: number) => void} */
    this.onFrame = () => {};
    this._track = null;
  }

  /** Subscribe and return the track. Safe to call once. */
  start() {
    this.ros.subscribe(this.topic, "sensor_msgs/msg/Image", (msg) => {
      const img = imageToImageData(msg, this.ctx);
      if (!img) return;                      // unsupported encoding, or short
      if (this.canvas.width !== img.width || this.canvas.height !== img.height) {
        this.canvas.width = img.width;
        this.canvas.height = img.height;
      }
      this.ctx.putImageData(img, 0, 0);
      this.frames++;
      this.lastStamp = msg.header && msg.header.stamp;
      this.onFrame(this.frames, img.width, img.height);
    }, this.throttleMs);

    // A zero-sized canvas produces a track with no frames, and the model then
    // waits forever for a source it will never get. Give it something to
    // sample until the first ROS frame lands.
    if (!this.canvas.width) { this.canvas.width = 16; this.canvas.height = 16; }

    const stream = this.canvas.captureStream(this.fps);
    this._track = stream.getVideoTracks()[0];
    return this._track;
  }

  get track() { return this._track; }

  stop() {
    this.ros.unsubscribe(this.topic);
    if (this._track) this._track.stop();
  }
}

/**
 * X2's output track -> /world_model/image_raw.
 *
 * Publishing at `fps` rather than per rendered frame: perception is capped at
 * 12 Hz and the scorer at 10, so anything faster is JSON on a websocket that
 * nothing reads. `maxWidth` downscales before publishing for the same reason
 * -- a 1080p frame is ~6 MiB before base64 adds a third.
 *
 * STAMPS: publish time, not the source frame's. WebRTC does not carry the
 * association, and this matches what x2_bridge_node.py did. It means the
 * scorer pairs a prediction with the labels nearest in ARRIVAL time; with a
 * moving rover and a model several frames behind, treat tight IoU differences
 * with suspicion until the label camera is stamped and matched properly.
 */
export class X2Sink {
  constructor(ros, topic = "/world_model/image_raw",
              { fps = 12, frameId = "x2_cam", maxWidth = 960 } = {}) {
    this.ros = ros;
    this.topic = topic;
    this.fps = fps;
    this.frameId = frameId;
    this.maxWidth = maxWidth;
    this.video = document.createElement("video");
    this.video.muted = true;
    this.video.playsInline = true;
    this.video.autoplay = true;
    this.canvas = document.createElement("canvas");
    this.ctx = this.canvas.getContext("2d", { willReadFrequently: true });
    this.published = 0;
    /** @type {(published: number, width: number, height: number) => void} */
    this.onPublish = () => {};
    this._timer = null;
  }

  async attach(track) {
    this.video.srcObject = new MediaStream([track]);
    try {
      await this.video.play();
    } catch (e) {
      // Autoplay policy only blocks audible media; a muted video should never
      // land here, but a failure to start is silent otherwise.
      throw new Error("could not start the output video element: " + e.message);
    }
    this.ros.advertise(this.topic, "sensor_msgs/msg/Image");
    clearInterval(this._timer);
    this._timer = setInterval(() => this._tick(), 1000 / this.fps);
  }

  _tick() {
    const vw = this.video.videoWidth, vh = this.video.videoHeight;
    if (!vw || !vh) return;                   // no frame decoded yet

    const scale = this.maxWidth && vw > this.maxWidth ? this.maxWidth / vw : 1;
    const w = Math.round(vw * scale), h = Math.round(vh * scale);
    if (this.canvas.width !== w || this.canvas.height !== h) {
      this.canvas.width = w; this.canvas.height = h;
    }
    this.ctx.drawImage(this.video, 0, 0, w, h);
    const img = this.ctx.getImageData(0, 0, w, h);
    this.ros.publish(this.topic, imageDataToMsg(img, this.frameId, nowStamp()));
    this.published++;
    this.onPublish(this.published, w, h);
  }

  stop() {
    clearInterval(this._timer);
    this._timer = null;
    this.video.srcObject = null;
  }
}

/**
 * The whole loop: ROS in, X2, ROS out, plus prompts and state.
 *
 * `adapter` is the only SDK contact point:
 *     publishTrack(name, track) -> Promise
 *     sendCommand(command, data) -> Promise
 *     onOutputTrack(fn)          -> calls fn(track) when the model's track arrives
 */
export class X2RosBridge {
  constructor(adapter, {
    rosUrl = "ws://localhost:9090",
    cameraTopic = "/panther/camera_left/image_raw",
    outputTopic = "/world_model/image_raw",
    promptTopic = "/world_model/prompt",
    stateTopic = "/world_model/state",
    sourceTrack = "source",
    outputTrack = "main_video",
    sourceFps = 24,
    outputFps = 12,
    maxWidth = 960,
  } = {}) {
    this.adapter = adapter;
    this.opts = { cameraTopic, outputTopic, promptTopic, stateTopic,
                  sourceTrack, outputTrack, sourceFps, outputFps, maxWidth };
    this.ros = new Rosbridge(rosUrl);
    this.source = null;
    this.sink = null;
    this.prompt = null;
    this.log = (msg) => console.log("[x2-ros]", msg);
  }

  async start() {
    await this.ros.connect();
    const o = this.opts;

    this.source = new UnitySource(this.ros, o.cameraTopic, { fps: o.sourceFps });
    const track = this.source.start();
    this.source.onFrame = (n, w, h) => {
      if (n === 1) this.log(`first camera frame: ${w}x${h} on ${o.cameraTopic}`);
    };
    await this.adapter.publishTrack(o.sourceTrack, track);
    this.log(`publishing ${o.cameraTopic} on track "${o.sourceTrack}"`);

    this.sink = new X2Sink(this.ros, o.outputTopic,
                           { fps: o.outputFps, maxWidth: o.maxWidth });
    this.sink.onPublish = (n, w, h) => {
      if (n === 1) this.log(`first X2 frame back out to ${o.outputTopic}: ${w}x${h}`);
    };
    this.adapter.onOutputTrack(async (outTrack) => {
      this.log(`model track "${o.outputTrack}" arrived`);
      await this.sink.attach(outTrack);
    });

    // The panel and the director both publish here, verbatim. A single changed
    // word invalidates the comparison (BUILD_SPEC 5.3), so this forwards the
    // string exactly as received and only enforces X2's own 1000-char cap.
    this.ros.subscribe(o.promptTopic, "std_msgs/msg/String", (msg) => {
      const text = (msg.data || "").trim();
      if (!text) return;
      this.prompt = text.length > 1000 ? text.slice(0, 1000) : text;
      if (text.length > 1000) {
        this.log(`prompt is ${text.length} chars; X2 caps at 1000. Truncating.`);
      }
      this.adapter.sendCommand("set_prompt", { prompt: this.prompt })
        .then(() => this.log(`prompt sent: ${this.prompt.slice(0, 50)}...`))
        .catch((e) => this.log("set_prompt failed: " + e.message));
    });

    // Keeps the panel's BRIDGE card alive, and says plainly that the frames
    // are coming through a browser rather than x2_bridge_node.
    this.ros.advertise(o.stateTopic, "std_msgs/msg/String");
    this._state = setInterval(() => this._publishState(), 1000);
  }

  _publishState() {
    const s = this.source, k = this.sink;
    this.ros.publish(this.opts.stateTopic, {
      data: JSON.stringify({
        generating: !!(k && k.published),
        via: "browser (myworld)",
        source_frames: s ? s.frames : 0,
        output_frames: k ? k.published : 0,
        // Only once a frame has actually been published. A fresh <canvas> is
        // 300x150, and reporting that as the model's output resolution puts a
        // plausible, entirely invented number on the panel.
        width: k && k.published ? k.canvas.width : undefined,
        height: k && k.published ? k.canvas.height : undefined,
        prompt: this.prompt,
      }),
    });
  }

  stop() {
    clearInterval(this._state);
    if (this.source) this.source.stop();
    if (this.sink) this.sink.stop();
    this.ros.close();
  }
}
