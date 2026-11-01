"use client";

import { useEffect, useRef, useState } from "react";

// captureStream is missing from some DOM lib typings.
type CapturableCanvas = HTMLCanvasElement & {
  captureStream?: (frameRate?: number) => MediaStream;
};

// Model's native pacing (matches the demo clips).
const STREAM_FPS = 24;

// Fixed 16:9 frame: the model picks its output resolution from the source
// aspect ratio, so this keeps preview, source and output consistent.
const FRAME_WIDTH = 1280;
const FRAME_HEIGHT = 720;

// Still image -> constant `source` stream. The image is drawn to a canvas that
// is repainted at the stream rate so captureStream keeps emitting it (the
// drag-to-animate setup). Produces the track; useSourcePublisher publishes it.
// The same canvas is the "original" pane in the stage.
export function ImageSource({
  imageUrl,
  onTrack,
}: {
  imageUrl: string;
  onTrack: (track: MediaStreamTrack | null) => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !imageUrl) return;
    let cancelled = false;
    let drawTimer: ReturnType<typeof setInterval> | null = null;
    let captured: MediaStreamTrack | null = null;
    setError(null);

    const img = new Image();
    img.onload = () => {
      if (cancelled) return;
      canvas.width = FRAME_WIDTH;
      canvas.height = FRAME_HEIGHT;
      const ctx = canvas.getContext("2d");
      if (!ctx) {
        setError("Canvas 2D is unavailable in this browser.");
        return;
      }
      // Center cover-crop into the 16:9 frame (no distortion or letterbox).
      const scale = Math.max(
        FRAME_WIDTH / img.naturalWidth,
        FRAME_HEIGHT / img.naturalHeight,
      );
      const cropWidth = FRAME_WIDTH / scale;
      const cropHeight = FRAME_HEIGHT / scale;
      const cropX = (img.naturalWidth - cropWidth) / 2;
      const cropY = (img.naturalHeight - cropHeight) / 2;
      const draw = () =>
        ctx.drawImage(
          img,
          cropX,
          cropY,
          cropWidth,
          cropHeight,
          0,
          0,
          FRAME_WIDTH,
          FRAME_HEIGHT,
        );
      draw();

      const capture = (canvas as CapturableCanvas).captureStream;
      if (!capture) {
        setError("This browser can't stream a canvas.");
        return;
      }
      const track =
        capture.call(canvas, STREAM_FPS).getVideoTracks()[0] ?? null;
      if (!track) return;
      track.contentHint = "detail";
      captured = track;
      onTrack(track);

      // captureStream only emits on repaint; redraw at the stream rate.
      drawTimer = setInterval(draw, 1000 / STREAM_FPS);
    };
    img.onerror = () => {
      if (!cancelled) setError("Could not load the image.");
    };
    img.src = imageUrl;

    return () => {
      cancelled = true;
      if (drawTimer) clearInterval(drawTimer);
      captured?.stop();
      onTrack(null);
    };
  }, [imageUrl, onTrack]);

  return (
    <>
      <canvas
        ref={canvasRef}
        className="absolute inset-0 h-full w-full object-contain"
      />
      {error && (
        <div className="absolute inset-0 flex items-center justify-center bg-black/70 p-4 text-center">
          <p className="text-xs text-red-400">{error}</p>
        </div>
      )}
    </>
  );
}
