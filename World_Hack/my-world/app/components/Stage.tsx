"use client";

import { X2MainVideoView } from "@reactor-models/x2";
import type { X2SourceMode, X2UiState } from "@/app/lib/types";
import { ImageSource } from "./ImageSource";
import { PointerOverlay } from "./PointerOverlay";
import { VideoSource } from "./VideoSource";

// Webcam mode shows only the edited output. Video/image modes show the source
// (which owns the published `source` track) beside the edited output, which
// carries the drag-to-steer overlay.
export function Stage({
  ui,
  mode,
  videoUrl,
  imageUrl,
  cleared,
  onTrack,
}: {
  ui: X2UiState;
  mode: X2SourceMode;
  videoUrl: string | null;
  imageUrl: string | null;
  // Covers the frozen last frame until generation runs again.
  cleared: boolean;
  onTrack: (track: MediaStreamTrack | null) => void;
}) {
  const outputAspect =
    ui.outputWidth && ui.outputHeight ? ui.outputWidth / ui.outputHeight : null;

  const statusRow = (
    <div className="absolute bottom-0 inset-x-0 flex min-w-0 items-center gap-3 bg-gradient-to-t from-black/60 p-3 font-mono text-xs text-zinc-400">
      <span className="shrink-0">
        {ui.generating ? "generating" : "waiting"}
      </span>
      {ui.outputWidth && ui.outputHeight && (
        <span className="shrink-0">
          {ui.outputWidth}×{ui.outputHeight}
        </span>
      )}
      {ui.hasReference && <span className="shrink-0">ref ✓</span>}
      {ui.activePrompt && (
        <span className="min-w-0 flex-1 truncate">"{ui.activePrompt}"</span>
      )}
    </div>
  );

  const editedPane = (label: string) => (
    <>
      <X2MainVideoView
        videoObjectFit="contain"
        className="absolute inset-0 h-full w-full"
      />
      {cleared && (
        <div
          data-testid="stage-cleared"
          className="absolute inset-0 bg-black"
        />
      )}
      <PointerOverlay outputAspect={outputAspect} enabled={ui.generating} />
      <span className="pointer-events-none absolute top-2 left-2 max-w-[80%] truncate rounded bg-black/60 px-1.5 py-0.5 font-mono text-xs text-zinc-400">
        {label}
      </span>
      {ui.generating && (
        <span className="pointer-events-none absolute top-2 right-2 rounded bg-black/60 px-1.5 py-0.5 font-mono text-xs text-zinc-500">
          drag to steer
        </span>
      )}
    </>
  );

  if (mode === "video" || mode === "image") {
    const sourceUrl = mode === "video" ? videoUrl : imageUrl;
    return (
      <section
        data-testid="stage-split"
        className="relative flex min-h-0 flex-col gap-3 max-lg:h-[40vh] lg:flex-1 lg:flex-row"
      >
        <div
          data-testid="pane-input"
          className="relative w-full min-h-0 flex-1 overflow-hidden rounded-lg border border-zinc-800 bg-black"
        >
          {sourceUrl ? (
            mode === "video" ? (
              <VideoSource videoUrl={sourceUrl} onTrack={onTrack} />
            ) : (
              <ImageSource imageUrl={sourceUrl} onTrack={onTrack} />
            )
          ) : (
            <div className="absolute inset-0 flex items-center justify-center p-4 text-center">
              <p className="text-xs text-zinc-500">
                {mode === "video"
                  ? "Select a video to stream into the model."
                  : "Select an image to stream into the model."}
              </p>
            </div>
          )}
          <span className="absolute top-2 left-2 rounded bg-black/60 px-1.5 py-0.5 font-mono text-xs text-zinc-400">
            original
          </span>
        </div>
        <div
          data-testid="pane-edited"
          className="relative w-full min-h-0 flex-1 overflow-hidden rounded-lg border border-zinc-800 bg-black"
        >
          {editedPane(ui.activePrompt ?? "edited")}
        </div>
        {statusRow}
      </section>
    );
  }

  return (
    <section className="relative flex-1 min-h-[40vh] lg:min-h-0 overflow-hidden rounded-lg border border-zinc-800 bg-black">
      {editedPane(ui.activePrompt ?? "edited")}
      {statusRow}
    </section>
  );
}
