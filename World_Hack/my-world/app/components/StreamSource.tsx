"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { MjpegSource } from "@/app/lib/ros/mjpeg.js";
import { Button, Panel, cn, FOCUS_RING } from "./ui";

// MJPEG stream -> the `source` track. Only produces a MediaStreamTrack and
// hands it up (see useSourcePublisher). The URL is rewritten through /api/wvs
// so frames are same-origin: a canvas with cross-origin pixels is tainted and
// cannot be captured.

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
    // "detail" keeps resolution stable (trades framerate under bandwidth dips).
    const track = src.start();
    track.contentHint = "detail";

    const sync = () =>
      setStats({
        status: src.status,
        bytes: src.bytes,
        frames: src.frames,
        size: src.canvas.width > 16 ? `${src.canvas.width}×${src.canvas.height}` : "",
      });
    src.onFrame = sync;
    // Before the first frame, show bytes moving.
    src.onProgress = () => {
      if (src.frames === 0) sync();
    };
    src.onError = setError;
    srcRef.current = src;

    // Preview the published canvas.
    if (holderRef.current) {
      holderRef.current.replaceChildren(src.canvas);
      src.canvas.className = "aspect-video w-full object-contain";
    }
    onTrack(track);
  }, [url, onTrack, stop]);

  // Free the slot on unmount.
  useEffect(() => () => stop(), [stop]);

  const running = !!srcRef.current;

  return (
    <Panel label="Stream">
      <div className="flex flex-col gap-2">
        <label className="flex flex-col gap-1">
          <span className="font-mono text-xs text-zinc-500">
            MJPEG url - copy any “Stream” link from http://localhost:8080
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
            <code>http://localhost:8080</code> - if it does and this stays at
            zero, the app cannot reach it (set <code>WEB_VIDEO_SERVER_URL</code>
            {" "}if web_video_server is not on localhost:8080).
          </p>
        )}
      </div>
    </Panel>
  );
}
