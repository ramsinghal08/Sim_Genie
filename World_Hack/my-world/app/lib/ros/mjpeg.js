/**
 * mjpeg.js -- an MJPEG stream -> a MediaStreamTrack X2 can consume.
 *
 * This is the ONLY input path: web_video_server already turns any ROS image
 * topic into MJPEG, so the browser never touches ROS to get pixels.
 *
 * WHY FETCH AND NOT <img>
 * ----------------------
 * An <img> pointed at multipart/x-mixed-replace decodes frames, but it reports
 * nothing: no per-frame event, no byte count, no HTTP status. When it produced
 * no frames the only signal was `naturalWidth === 0`, which is identical for a
 * 404, a wrong topic, a stalled server, and a browser that decided not to
 * decode. Hours went into guessing between those.
 *
 * Reading the stream with fetch() makes every one of them nameable -- status,
 * bytes, parts -- and removes the dependency on the browser's multipart <img>
 * behaviour entirely.
 *
 * It also solves canvas taint for free. A cross-origin <img> drawn to a canvas
 * taints it, and a tainted canvas CANNOT be captured -- which would break the
 * whole point, since captureStream() is what feeds X2. A Blob assembled from
 * fetched bytes is same-origin data, so the canvas stays clean.
 */

/** Find `needle` in `hay` starting at `from`. -1 if absent. */
function indexOfBytes(hay, needle, from = 0) {
  outer: for (let i = from; i <= hay.length - needle.length; i++) {
    for (let j = 0; j < needle.length; j++) {
      if (hay[i + j] !== needle[j]) continue outer;
    }
    return i;
  }
  return -1;
}

const ENC = new TextEncoder();
const DEC = new TextDecoder();

export class MjpegSource {
  /**
   * @param {string} url  full stream URL (same-origin, e.g. via /api/wvs)
   * @param {{fps?: number}} [opts]
   */
  constructor(url, { fps = 24 } = {}) {
    this.url = url;
    this.fps = fps;

    this.canvas = document.createElement("canvas");
    this.ctx = this.canvas.getContext("2d");

    /** HTTP status of the stream request, once known. */
    this.status = 0;
    /** Bytes read off the wire. */
    this.bytes = 0;
    /** JPEG parts successfully decoded and drawn. */
    this.frames = 0;

    /** @type {(frames: number, width: number, height: number) => void} */
    this.onFrame = () => {};
    /** @type {(message: string) => void} */
    this.onError = () => {};
    /** @type {() => void} */
    this.onProgress = () => {};

    this._track = null;
    this._abort = null;
  }

  /** Begin reading, and return the track immediately. */
  start() {
    // captureStream needs a canvas with a size; the first decoded frame
    // resizes it. Without this the track exists but carries nothing, and the
    // model waits forever for a source it will never get.
    if (!this.canvas.width) {
      this.canvas.width = 16;
      this.canvas.height = 16;
    }
    const stream = this.canvas.captureStream(this.fps);
    this._track = stream.getVideoTracks()[0];

    this._abort = new AbortController();
    void this._run();
    return this._track;
  }

  get track() {
    return this._track;
  }

  async _run() {
    let res;
    try {
      res = await fetch(this.url, {
        signal: this._abort.signal,
        cache: "no-store",
      });
    } catch (e) {
      if (this._abort.signal.aborted) return;
      this.onError(`cannot reach ${this.url}: ${e instanceof Error ? e.message : e}`);
      return;
    }

    this.status = res.status;
    if (!res.ok) {
      this.onError(`HTTP ${res.status} from ${this.url}`);
      return;
    }

    const contentType = res.headers.get("content-type") || "";
    const match = /boundary=([^;]+)/i.exec(contentType);
    if (!match) {
      this.onError(
        `not an MJPEG stream — Content-Type is "${contentType || "(none)"}"`,
      );
      return;
    }

    // web_video_server declares `boundary=--boundarydonotcross` and then
    // writes `--boundarydonotcross`, which is one `--` short of what the
    // multipart spec says. Searching for the DECLARED string handles both
    // that and a spec-correct server, because the declared text appears
    // verbatim inside the delimiter either way.
    const boundary = ENC.encode(match[1].trim().replace(/^"|"$/g, ""));
    const headerEnd = ENC.encode("\r\n\r\n");

    const reader = res.body.getReader();
    let buf = new Uint8Array(0);

    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (done || this._abort.signal.aborted) break;

        this.bytes += value.length;
        const merged = new Uint8Array(buf.length + value.length);
        merged.set(buf, 0);
        merged.set(value, buf.length);
        buf = merged;
        this.onProgress();

        // Drain every complete part currently in the buffer.
        for (;;) {
          const start = indexOfBytes(buf, boundary);
          if (start < 0) break;
          const hEnd = indexOfBytes(buf, headerEnd, start);
          if (hEnd < 0) break;                       // headers still arriving

          const headers = DEC.decode(buf.subarray(start, hEnd));
          const lenMatch = /content-length:\s*(\d+)/i.exec(headers);
          const bodyStart = hEnd + headerEnd.length;

          let bodyEnd;
          if (lenMatch) {
            bodyEnd = bodyStart + parseInt(lenMatch[1], 10);
          } else {
            // No Content-Length: the part ends where the next one begins.
            const next = indexOfBytes(buf, boundary, bodyStart);
            if (next < 0) break;                     // wait for the next boundary
            bodyEnd = next - 2;                      // strip the trailing CRLF
          }
          if (buf.length < bodyEnd) break;           // body still arriving

          await this._draw(buf.subarray(bodyStart, bodyEnd));
          buf = buf.slice(bodyEnd);
        }

        // A boundary that never matches would otherwise grow this forever.
        if (buf.length > 32 * 1024 * 1024) {
          this.onError("no multipart boundary found in 32 MB — wrong stream format?");
          return;
        }
      }
    } catch (e) {
      if (!this._abort.signal.aborted) {
        this.onError(`stream ended: ${e instanceof Error ? e.message : e}`);
      }
    }
  }

  async _draw(jpegBytes) {
    let bitmap;
    try {
      // A Blob built here is OUR data, so drawing it never taints the canvas
      // -- unlike a cross-origin <img>, which would make captureStream fail.
      bitmap = await createImageBitmap(
        new Blob([jpegBytes], { type: "image/jpeg" }),
      );
    } catch (e) {
      return;                                        // a torn part; skip it
    }
    if (this.canvas.width !== bitmap.width || this.canvas.height !== bitmap.height) {
      this.canvas.width = bitmap.width;
      this.canvas.height = bitmap.height;
    }
    this.ctx.drawImage(bitmap, 0, 0);
    bitmap.close();
    this.frames++;
    this.onFrame(this.frames, this.canvas.width, this.canvas.height);
  }

  stop() {
    if (this._abort) this._abort.abort();
    if (this._track) this._track.stop();
  }
}
