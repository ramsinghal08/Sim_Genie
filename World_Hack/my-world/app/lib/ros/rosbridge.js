/**
 * Minimal rosbridge v2 JSON protocol client plus raw sensor_msgs/Image
 * encode/decode helpers. No dependencies or build step.
 *
 * Images are raw sensor_msgs/Image only (no CompressedImage); rosbridge sends
 * uint8[] as base64.
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
    this.handlers = new Map(); // topic -> fn(msg)
    this.advertised = new Set();
    /** @type {(status: string) => void} */
    this.onStatus = () => {};
  }

  connect() {
    return new Promise((resolve, reject) => {
      const ws = new WebSocket(this.url);
      this.ws = ws;
      ws.onopen = () => { this.onStatus("connected"); resolve(this); };
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

// image decode

/** rosbridge uint8[] -> Uint8Array. Base64 normally; some builds send an array. */
export function bytesOf(data) {
  if (typeof data !== "string") return Uint8Array.from(data);
  const bin = atob(data);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

/**
 * sensor_msgs/Image -> ImageData, honouring encoding and row padding
 * (`step` may exceed width*channels). Returns null for unsupported encodings
 * or truncated data.
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

// image encode

const B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

/** Uint8Array -> base64 (avoids the argument limit of String.fromCharCode(...bytes)). */
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
 * ImageData -> sensor_msgs/Image (rgb8) for rosbridge. Alpha is dropped so the
 * payload matches the declared encoding. `stamp` is a ROS Time ({sec, nanosec}).
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
