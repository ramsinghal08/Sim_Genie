"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { MjpegSource } from "@/app/lib/ros/mjpeg.js";
import { Button, Panel, cn, FOCUS_RING } from "./ui";

// An MJPEG stream -> the `source` track.
//
// Same contract as WebcamSource: this only PRODUCES a MediaStreamTrack and
// hands it up. useSourcePublisher stays the single owner of the `source` slot,
// so switching modes cannot race two publishers.
//
// web_video_server turns any ROS image topic into MJPEG, so this needs no ROS
// client at all -- no rosbridge, no websocket, no topic list. Paste the URL of
// any stream listed at http://localhost:8080 and it becomes the model's input.
//
// THE URL IS REWRITTEN THROUGH /api/wvs ON PURPOSE. The frames land in a
// <canvas> whose captureStream() feeds WebRTC, and a canvas that drew
// cross-origin pixels is tainted -- a tainted canvas cannot be captured, which
// would break the one thing this component exists to do. Going through the
// app's own origin sidesteps it without depending on web_video_server's CORS
// headers.

const DEFAULT_URL =
  "http://localhost:8080/stream?topic=/unity/camera/image_raw" +
  "&type=mjpeg&quality=70&default_transport=raw";

/** Any absolute web_video_server URL -> the same path under our own origin. */
function throughProxy(raw: string): string {
  const trimmed = raw.trim();
  if (trimmed.startsWith("/api/wvs")) return trimmed;
  try {
    const u = new URL(trimmed, window.location.origin);
    return `/api/wvs${u.pathname}${u.search}`;
  } catch {
    return trimmed;
  }
}

function humanBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export function StreamSource({
  onTrack,
}: {
  onTrack: (track: MediaStreamTrack | null) => void;
}) {
  const [url, setUrl] = useState(DEFAULT_URL);
  const [error, setError] = useState<string | null>(null);
  const [stats, setStats] = useState({ status: 0, bytes: 0, frames: 0, size: "" });

  const holderRef = useRef<HTMLDivElement>(null);
  const srcRef = useRef<InstanceType<typeof MjpegSource> | null>(null);

  const stop = useCallback(() => {
    if (srcRef.current) {
      srcRef.current.stop();
      srcRef.current = null;
    }
    onTrack(null);
    setError(null);
    setStats({ status: 0, bytes: 0, frames: 0, size: "" });
  }, [onTrack]);

  const start = useCallback(() => {
    stop();
    const src = new MjpegSource(throughProxy(url), { fps: 24 });
    // The model wants a stable resolution; browsers otherwise ramp resolution
    // at stream start and on bandwidth dips. "detail" holds it and trades
    // framerate instead.
    const track = src.start();
    track.contentHint = "detail";

    const sync = () =>
      setStats({
        status: src.status,
        bytes: src.bytes,
        frames: src.frames,
        size: src.canvas.width > 16 ? `${src.canvas.width}×${src.canvas.height}` : "",
      });
    // Bytes tick far faster than anything worth re-rendering for; frames are
    // the interesting event, and progress is sampled for the byte counter.
    src.onFrame = sync;
    src.onProgress = () => {
      if (src.frames === 0) sync();      // so a stream with no decodable part
    };                                    // still shows bytes moving
    src.onError = setError;
    srcRef.current = src;

    // Show the frames actually being published: this canvas IS the track.
    if (holderRef.current) {
      holderRef.current.replaceChildren(src.canvas);
      src.canvas.className = "aspect-video w-full object-contain";
    }
    onTrack(track);
  }, [url, onTrack, stop]);

  // Unmount must free the slot, or a mode switch leaves a dead canvas track
  // published and the model keeps editing a frozen frame.
  useEffect(() => () => stop(), [stop]);

  const running = !!srcRef.current;

  return (
    <Panel label="Stream">
      <div className="flex flex-col gap-2">
        <label className="flex flex-col gap-1">
          <span className="font-mono text-xs text-zinc-500">
            MJPEG url — copy any “Stream” link from http://localhost:8080
          </span>
          <input
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            disabled={running}
            spellCheck={false}
            className={cn(
              "rounded border border-zinc-700 bg-zinc-900 px-2 py-1 font-mono text-xs text-zinc-200 disabled:opacity-50",
              FOCUS_RING,
            )}
          />
        </label>

        <div className="flex items-center gap-2">
          {running ? (
            <Button variant="secondary" size="sm" onClick={stop}>
              Stop
            </Button>
          ) : (
            <Button size="sm" onClick={start}>
              Start
            </Button>
          )}
        </div>

        <div
          ref={holderRef}
          className="overflow-hidden rounded-md border border-zinc-800 bg-black"
        />

        {/* Everything the stream knows about itself. "No frames" used to be
            the only thing this could say, for four different causes. */}
        <p className="font-mono text-xs text-zinc-500">
          {!running
            ? "stopped"
            : stats.frames > 0
              ? `HTTP ${stats.status} · ${stats.frames} frames · ${humanBytes(stats.bytes)}${
                  stats.size ? ` · ${stats.size}` : ""
                }`
              : stats.bytes > 0
                ? `HTTP ${stats.status} · ${humanBytes(stats.bytes)} received, no frame decoded yet`
                : stats.status
                  ? `HTTP ${stats.status} · waiting for data`
                  : "connecting…"}
        </p>

        {error && <p className="text-xs text-red-400">{error}</p>}

        {running && !error && stats.bytes === 0 && (
          <p className="text-xs text-amber-400">
            Nothing has arrived yet. Check the stream plays on its own at{" "}
            <code>http://localhost:8080</code> — if it does and this stays at
            zero, the app cannot reach it (set <code>WEB_VIDEO_SERVER_URL</code>
            {" "}if web_video_server is not on localhost:8080).
          </p>
        )}
      </div>
    </Panel>
  );
}
