// Input source streamed into the `source` track: "webcam", "video" (a chosen
// clip), "image" (a still repeated as a constant 24 fps feed for
// drag-to-animate), or "stream" (MJPEG over HTTP from web_video_server, see
// components/StreamSource).
export type X2SourceMode = "webcam" | "video" | "image" | "stream";

// UI projection of the model session, reduced from the `state_update`
// snapshot. `referenceAccepted` comes from the discrete
// `reference_image_accepted` ack instead.
export interface X2UiState {
  /** Whether a generation run is currently producing `main_video` frames. */
  generating: boolean;
  /** The active editing instruction, or null when none is set. */
  activePrompt: string | null;
  /** Output resolution, fixed for the session once the first run starts. */
  outputWidth: number | null;
  outputHeight: number | null;
  /** Whether a reference image is currently conditioning generation. */
  hasReference: boolean;
  /** Current source-frame policy (see the set_keep_backlog command). */
  keepBacklog: boolean;
  /** Last reference_image_accepted ack ({width, height} of the decoded image). */
  referenceAccepted: { width: number; height: number } | null;
}

export const DEFAULT_UI_STATE: X2UiState = {
  generating: false,
  activePrompt: null,
  outputWidth: null,
  outputHeight: null,
  hasReference: false,
  keepBacklog: false,
  referenceAccepted: null,
};

// Drag pointer echoed in every `state_update`. Kept out of X2UiState because it
// changes ~30 Hz and would re-render the whole workspace; PointerPanel
// subscribes to it separately.
export interface X2PointerReadout {
  /** Drag-pointer position, normalized to the output frame (0..1). */
  x: number;
  y: number;
  /** Whether a drag gesture is currently steering the edited subject. */
  active: boolean;
}

export const DEFAULT_POINTER_READOUT: X2PointerReadout = {
  x: 0.5,
  y: 0.5,
  active: false,
};
