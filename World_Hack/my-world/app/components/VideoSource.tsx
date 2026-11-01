"use client";

import { useEffect, useRef, useState } from "react";

// captureStream is missing from some DOM lib typings (plus Firefox's
// mozCaptureStream).
type CapturableVideo = HTMLVideoElement & {
  captureStream?: () => MediaStream;
  mozCaptureStream?: () => MediaStream;
};

// Video file -> `source` track. The clip plays and loops in a <video> whose
// frames are captured with captureStream(); useSourcePublisher publishes the
// track. The same element is the "original" pane in the stage.
export function VideoSource({
  videoUrl,
  onTrack,
}: {
  videoUrl: string;
  onTrack: (track: MediaStreamTrack | null) => void;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [error, setError] = useState<string | null>(null);

  // Capture frames once metadata is available.
  useEffect(() => {
    const v = videoRef.current;
    if (!v || !videoUrl) return;
    let cancelled = false;
    let captured: MediaStreamTrack | null = null;

    const startCapture = () => {
      if (cancelled) return;
      const cap = v as CapturableVideo;
      const capture = cap.captureStream ?? cap.mozCaptureStream;
      if (!capture) {
        setError("This browser can't stream a video file.");
        return;
      }
      const track = capture.call(v).getVideoTracks()[0] ?? null;
      if (!track) return;
      track.contentHint = "detail";
      captured = track;
      onTrack(track);
    };

    if (v.readyState >= 1 /* HAVE_METADATA */) startCapture();
    else v.addEventListener("loadedmetadata", startCapture, { once: true });

    return () => {
      cancelled = true;
      v.removeEventListener("loadedmetadata", startCapture);
      captured?.stop();
      onTrack(null);
    };
  }, [videoUrl, onTrack]);

  return (
    <>
      <video
        ref={videoRef}
        src={videoUrl}
        autoPlay
        loop
        muted
        playsInline
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
