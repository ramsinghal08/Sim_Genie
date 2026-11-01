"use client";

import { useState } from "react";
import {
  ClipDownloadButton,
  ClipPlayer,
  RecordingError,
  useReactor,
  type Clip,
} from "@reactor-team/js-sdk";
import { Button, cn, EYEBROW, Icon, PANEL, Panel } from "./ui";

// "Snap clip" panel: captures the last `durationSeconds` of the live session
// and shows it in a modal with the SDK <ClipPlayer> and a download button.
// Uses only @reactor-team/js-sdk (recording is a base-SDK feature). Player and
// download errors are routed into the inline error line.
export interface SnapClipProps {
  /** Length of the snap, in seconds. Default 10. */
  durationSeconds?: number;
  /**
   * Suggested filename for the saved MP4. Default
   * `reactor-clip-<unix-seconds>.mp4`.
   */
  filename?: string;
  /** Optional override for the button label. */
  label?: string;
}

export function SnapClip({
  durationSeconds = 10,
  filename,
  label,
}: SnapClipProps) {
  const { status, requestClip } = useReactor((s) => ({
    status: s.status,
    requestClip: s.requestClip,
  }));

  const [clip, setClip] = useState<Clip | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (status !== "ready") return null;

  async function snap() {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const c = await requestClip(durationSeconds);
      setClip(c);
    } catch (e) {
      setError(
        e instanceof RecordingError
          ? `${e.code}: ${e.reason}`
          : e instanceof Error
            ? e.message
            : String(e),
      );
    } finally {
      setBusy(false);
    }
  }

  const downloadName =
    filename ?? `reactor-clip-${Math.floor(Date.now() / 1000)}.mp4`;

  return (
    <Panel label="Capture">
      <Button
        variant="primary"
        size="md"
        onClick={snap}
        disabled={busy}
        className="w-full"
        leadingIcon={<Icon name="scissors" />}
      >
        {busy ? "Capturing…" : (label ?? `Snap last ${durationSeconds}s`)}
      </Button>
      {error && <p className="mt-2 text-xs text-red-400">{error}</p>}

      {clip && (
        <ClipModal
          clip={clip}
          filename={downloadName}
          onClose={() => setClip(null)}
          onError={(e) => setError(e.message)}
          onDownloaded={() => setError(null)}
        />
      )}
    </Panel>
  );
}

function ClipModal({
  clip,
  filename,
  onClose,
  onError,
  onDownloaded,
}: {
  clip: Clip;
  filename: string;
  onClose: () => void;
  onError: (error: Error) => void;
  onDownloaded: () => void;
}) {
  return (
    <div
      onClick={onClose}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4"
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className={cn(
          PANEL,
          "flex w-full max-w-2xl flex-col gap-3 bg-zinc-950 p-4 shadow-xl",
        )}
      >
        <div className="flex items-center justify-between gap-3">
          <span className={EYEBROW}>Clip · {clip.kind}</span>
          <Button
            variant="secondary"
            size="sm"
            onClick={onClose}
            leadingIcon={<Icon name="x" />}
          >
            Close
          </Button>
        </div>

        <ClipPlayer
          clip={clip}
          onError={onError}
          className="w-full overflow-hidden rounded-lg border border-zinc-800"
        />

        <div className="flex justify-end">
          <ClipDownloadButton
            clip={clip}
            filename={filename}
            onSuccess={onDownloaded}
            onError={onError}
          />
        </div>
      </div>
    </div>
  );
}
