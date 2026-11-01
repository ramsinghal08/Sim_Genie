"use client";

import { useEffect, useRef, useState } from "react";
import { useX2 } from "@reactor-models/x2";
import type { X2SourceMode } from "@/app/lib/types";
import { Button, Panel, SegmentedToggle, Switch } from "./ui";
import { ImagePicker } from "./ImagePicker";
import { VideoPicker } from "./VideoPicker";
import { WebcamSource } from "./WebcamSource";
import { StreamSource } from "./StreamSource";

// Source picker. X2 starts generating once a prompt is set and source frames
// arrive. Each mode only produces a MediaStreamTrack; useSourcePublisher owns
// the `source` slot. Webcam and stream stay mounted while generating so the
// feed keeps flowing; video and image modes only pick media (the stage plays
// it). The source is fixed for a run: Reset to change it.
export function SourcePanel({
  generating,
  keepBacklog,
  mode,
  onModeChange,
  onSelectVideo,
  onSelectImage,
  onTrack,
}: {
  generating: boolean;
  /** Model-reported keep_backlog policy (from state_update). */
  keepBacklog: boolean;
  mode: X2SourceMode;
  onModeChange: (m: X2SourceMode) => void;
  onSelectVideo: (url: string, name: string) => void;
  onSelectImage: (url: string, name: string) => void;
  onTrack: (track: MediaStreamTrack | null) => void;
}) {
  const { reset, setKeepBacklog, status } = useX2();
  const ready = status === "ready";

  // `keepBacklog` is the model's echoed policy: false drops stale frames
  // (low latency), true consumes every frame (smoother, growing delay).
  // `pending` optimistically drives the switch until the echo confirms it, or
  // a timeout falls back to the echoed value (a rejected command only surfaces
  // as a separate command_error).
  const [pending, setPending] = useState<boolean | null>(null);
  const pendingTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const displayKeepBacklog = pending ?? keepBacklog;

  useEffect(() => {
    if (pending !== null && keepBacklog === pending) {
      setPending(null);
      if (pendingTimer.current) clearTimeout(pendingTimer.current);
    }
  }, [keepBacklog, pending]);

  useEffect(() => {
    return () => {
      if (pendingTimer.current) clearTimeout(pendingTimer.current);
    };
  }, []);

  const toggleKeepBacklog = (value: boolean) => {
    setPending(value);
    if (pendingTimer.current) clearTimeout(pendingTimer.current);
    pendingTimer.current = setTimeout(() => setPending(null), 4000);
    setKeepBacklog({ keep_backlog: value }).catch(console.error);
  };

  const handleModeChange = (m: X2SourceMode) => {
    if (generating) return;
    onModeChange(m);
  };

  return (
    <Panel label="Source">
      {!generating && (
        <SegmentedToggle
          aria-label="Input source"
          value={mode}
          onChange={handleModeChange}
          options={[
            { value: "webcam", label: "Webcam" },
            { value: "video", label: "Video" },
            { value: "image", label: "Image" },
            { value: "stream", label: "Stream" },
          ]}
        />
      )}

      {mode === "webcam" ? (
        <div className={generating ? "" : "mt-3"}>
          <WebcamSource onTrack={onTrack} />
        </div>
      ) : mode === "stream" ? (
        <div className={generating ? "" : "mt-3"}>
          <StreamSource onTrack={onTrack} />
        </div>
      ) : mode === "video" ? (
        !generating && (
          <div className="mt-3">
            <VideoPicker onSelect={onSelectVideo} />
          </div>
        )
      ) : (
        !generating && (
          <div className="mt-3">
            <ImagePicker onSelect={onSelectImage} />
          </div>
        )
      )}

      <div className="mt-3 flex items-start justify-between gap-3">
        <span className="min-w-0 text-xs text-zinc-400">
          Keep frame backlog
          <span className="block text-zinc-600">
            {pending !== null
              ? "Applying…"
              : displayKeepBacklog
                ? "Every frame: smoother motion, growing delay."
                : "Newest frames: bounded latency."}
          </span>
        </span>
        <Switch
          aria-label="Keep frame backlog"
          checked={displayKeepBacklog}
          disabled={!ready}
          onChange={toggleKeepBacklog}
        />
      </div>

      {generating ? (
        <div className="mt-3 flex items-center gap-2">
          <Button
            variant="secondary"
            size="sm"
            disabled={!ready}
            onClick={() => reset().catch(console.error)}
          >
            Reset
          </Button>
          <span className="text-xs text-zinc-500">
            Editing - reset to change the source
          </span>
        </div>
      ) : (
        <p className="mt-3 text-xs text-zinc-500">
          {ready
            ? "Generation starts once a prompt is set and frames are streaming."
            : "Connect, then set a prompt to start."}
        </p>
      )}
    </Panel>
  );
}
