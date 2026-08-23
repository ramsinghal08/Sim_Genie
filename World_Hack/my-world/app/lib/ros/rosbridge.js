/**
 * rosbridge.js -- the rosbridge v2 JSON protocol, just the parts this needs.
 *
 * No dependencies, no framework, no build step. Import it from a Next page, a
 * plain <script type="module">, or a test harness; it only touches WebSocket
 * and the DOM types it is handed.
 *
 * WHY THIS EXISTS AT ALL
 * ----------------------
 * The Reactor SDK is a browser SDK: it authenticates with a JWT minted
 * server-side and moves video over WebRTC. The rig is ROS 2. rosbridge is the
 * one place those two meet without a native module -- so the browser becomes
 * the bridge, and x2_bridge_node.py is bypassed on the live path.
 *
 * Everything downstream (perception, scoring, director, the control panel) is
 * unchanged, because all of it only ever spoke ROS topics.
 *
 * IMAGE ENCODING
 * --------------
 * rosbridge sends uint8[] as base64 by default and accepts it the same way.
 * Raw sensor_msgs/Image only -- no CompressedImage anywhere in this rig, and a
 * `<topic>/compressed` that nothing publishes is the quietest failure there is.
 */

const CHANNELS = {
  mono8: 1, "8UC1": 1,
  rgb8: 3, bgr8: 3, "8UC3": 3,
  rgba8: 4, bgra8: 4, "8UC4": 4,
};
const BGR_ORDER = { bgr8: true, bgra8: true };

export class Rosbridge {
  constructor(url = "ws://localhost:9090") {
    this.url = url;
    this.ws = null;
    this.handlers = new Map();      // topic -> fn(msg)
    this.advertised = new Set();
    /** @type {(status: string) => void} */
    this.onStatus = () => {};
  }

  connect() {
    return new Promise((resolve, reject) => {
      const ws = new WebSocket(this.url);
      this.ws = ws;
      ws.onopen = () => { this.onStatus("connected"); resolve(this); };
      // A socket error here is almost always "rosbridge is not running", which
      // is worth saying plainly rather than as an event object.
      ws.onerror = () => {
        this.onStatus("cannot reach rosbridge at " + this.url);
        reject(new Error("cannot reach rosbridge at " + this.url));
      };
      ws.onclose = () => this.onStatus("disconnected");
      ws.onmessage = (ev) => {
        let m;
        try { m = JSON.parse(ev.data); } catch (e) { return; }
        if (m.op === "status" && m.level === "error") {
          this.onStatus("rosbridge: " + m.msg);
          return;
        }
        if (m.op !== "publish") return;
        const fn = this.handlers.get(m.topic);
        if (fn) fn(m.msg);
      };
    });
  }

  get ready() {
    return !!this.ws && this.ws.readyState === WebSocket.OPEN;
  }

  send(obj) {
    if (this.ready) this.ws.send(JSON.stringify(obj));
  }

  subscribe(topic, type, handler, throttleMs = 0) {
    this.handlers.set(topic, handler);
    this.send({ op: "subscribe", topic, type,
                throttle_rate: throttleMs, queue_length: 1 });
  }

  unsubscribe(topic) {
    this.handlers.delete(topic);
    this.send({ op: "unsubscribe", topic });
  }

  advertise(topic, type) {
    if (this.advertised.has(topic)) return;
    this.advertised.add(topic);
    this.send({ op: "advertise", topic, type });
  }

  publish(topic, msg) {
    this.send({ op: "publish", topic, msg });
  }

  close() {
    try { this.ws && this.ws.close(); } catch (e) { /* already gone */ }
  }
}

// ------------------------------------------------------------ image decode

/** rosbridge uint8[] -> Uint8Array. Base64 normally; some builds send an array. */
export function bytesOf(data) {
  if (typeof data !== "string") return Uint8Array.from(data);
  const bin = atob(data);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

/**
 * sensor_msgs/Image -> ImageData, honouring encoding and row padding.
 *
 * `step` is bytes per ROW and may exceed width*channels when the publisher
 * aligns rows. Assuming it away shears the image diagonally rather than
 * failing, which is the kind of bug that gets blamed on the camera.
 *
 * Returns null rather than guessing on an encoding this rig does not move.
 */
export function imageToImageData(msg, ctx) {
  const w = msg.width | 0, h = msg.height | 0;
  const enc = msg.encoding || "rgb8";
  const ch = CHANNELS[enc];
  if (!w || !h || !ch) return null;

  const bytes = bytesOf(msg.data);
  const step = (msg.step | 0) || w * ch;
  if (bytes.length < step * h) return null;

  const out = ctx.createImageData(w, h);
  const px = out.data;
  const bgr = BGR_ORDER[enc] === true;
  for (let y = 0; y < h; y++) {
    let s = y * step, d = y * w * 4;
    for (let x = 0; x < w; x++, s += ch, d += 4) {
      if (ch === 1) {
        const v = bytes[s];
        px[d] = v; px[d + 1] = v; px[d + 2] = v;
      } else if (bgr) {
        px[d] = bytes[s + 2]; px[d + 1] = bytes[s + 1]; px[d + 2] = bytes[s];
      } else {
        px[d] = bytes[s]; px[d + 1] = bytes[s + 1]; px[d + 2] = bytes[s + 2];
      }
      px[d + 3] = 255;
    }
  }
  return out;
}

// ------------------------------------------------------------ image encode

const B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

/** Uint8Array -> base64, without a round trip through a binary string.
 *
 * btoa(String.fromCharCode(...bytes)) blows the argument limit on anything
 * bigger than a thumbnail, and building the string with a loop is slower than
 * this for a full frame at video rate.
 */
export function base64Of(bytes) {
  let out = "";
  const n = bytes.length;
  let i = 0;
  for (; i + 2 < n; i += 3) {
    const v = (bytes[i] << 16) | (bytes[i + 1] << 8) | bytes[i + 2];
    out += B64[(v >> 18) & 63] + B64[(v >> 12) & 63]
         + B64[(v >> 6) & 63] + B64[v & 63];
  }
  const rem = n - i;
  if (rem === 1) {
    const v = bytes[i] << 16;
    out += B64[(v >> 18) & 63] + B64[(v >> 12) & 63] + "==";
  } else if (rem === 2) {
    const v = (bytes[i] << 16) | (bytes[i + 1] << 8);
    out += B64[(v >> 18) & 63] + B64[(v >> 12) & 63] + B64[(v >> 6) & 63] + "=";
  }
  return out;
}

/**
 * ImageData -> sensor_msgs/Image (rgb8), ready for rosbridge.
 *
 * The alpha channel is dropped: rgb8 is what every subscriber in this rig
 * reads, and a 4-channel payload described as rgb8 would be decoded as
 * garbage with no error anywhere.
 *
 * `stamp` should come from the SOURCE frame, not from now(): the scorer pairs
 * the prediction with the label frame it belongs to, and a fresh stamp here
 * pairs it with whatever Unity rendered while the model was thinking.
 */
export function imageDataToMsg(img, frameId = "x2_cam", stamp = null) {
  const { width, height, data } = img;
  const rgb = new Uint8Array(width * height * 3);
  for (let i = 0, o = 0; i < data.length; i += 4, o += 3) {
    rgb[o] = data[i]; rgb[o + 1] = data[i + 1]; rgb[o + 2] = data[i + 2];
  }
  return {
    header: {
      stamp: stamp || { sec: 0, nanosec: 0 },
      frame_id: frameId,
    },
    height, width,
    encoding: "rgb8",
    is_bigendian: 0,
    step: width * 3,
    data: base64Of(rgb),
  };
}
