"use client";

// Reference frontend for the X2 real-time video-to-video editing model.
// Edits the `source` track live, steered by a swappable text prompt, an
// optional reference image, and a drag-to-steer pointer on the output.
import { useEffect, useRef, useState } from "react";
import {
  X2Provider,
  useX2,
  useX2CommandError,
  useX2GenerationStopped,
  useX2ReferenceImageAccepted,
  useX2StateUpdate,
} from "@reactor-models/x2";
import {
  DEFAULT_UI_STATE,
  type X2SourceMode,
  type X2UiState,
} from "@/app/lib/types";
import { reduce } from "@/app/lib/state";
import { Header } from "./components/Header";
import { StatusBadge } from "./components/StatusBadge";
import { CommandError } from "./components/CommandError";
import { SourcePanel } from "./components/SourcePanel";
import { ReferenceImage } from "./components/ReferenceImage";
import { PointerPanel } from "./components/PointerPanel";
import { Prompt } from "./components/Prompt";
import { Stage } from "./components/Stage";
import { SnapClip } from "./components/SnapClip";
import { useSourcePublisher } from "./components/useSourcePublisher";
import { useRosOutput } from "./components/useRosOutput";
import { REACTOR_API_URL } from "@/app/lib/config";

// JWT resolver for <X2Provider getJwt>. The SDK calls it on every Reactor API
// request; the token route sets Cache-Control so the browser caches the JWT.
async function fetchToken(): Promise<string> {
  const r = await fetch("/api/reactor/token");
  if (!r.ok) {
    const body = (await r.json().catch(() => ({}))) as { error?: string };
    throw new Error(body.error ?? `Token fetch failed: ${r.status}`);
  }
  const { jwt } = (await r.json()) as { jwt: string };
  return jwt;
}

// No `autoConnect`: the user connects explicitly.
export function X2App() {
  return (
    <X2Provider
      apiUrl={REACTOR_API_URL}
      getJwt={fetchToken}
      connectOptions={{ autoConnect: false }}
    >
      <Workspace />
    </X2Provider>
  );
}

const BANNER_TTL_MS = 6000;

// The model broadcasts a full `state_update` snapshot on connect and after each
// change, reduced into X2UiState by lib/state.ts. generation_stopped,
// reference_image_accepted and command_error are handled as discrete events.
function Workspace() {
  const { status } = useX2();

  const [ui, setUi] = useState<X2UiState>(DEFAULT_UI_STATE);
  // All source modes feed the same `source` track.
  const [mode, setMode] = useState<X2SourceMode>("webcam");

  // The active source hands its track here; useSourcePublisher is the single
  // owner of the `source` slot, so mode switches can't race two publishers.
  const [sourceTrack, setSourceTrack] = useState<MediaStreamTrack | null>(null);
  const publishError = useSourcePublisher(sourceTrack);

  // Return leg: publishes model output to /world_model/image_raw and forwards
  // /world_model/prompt into the model. Off by default (no websocket opened).
  const [rosLoop, setRosLoop] = useState(false);
  const rosOut = useRosOutput({ enabled: rosLoop });

  // Selected video/image URLs (object URL or preset path); setters revoke
  // replaced object URLs.
  const [videoUrl, setVideoUrlState] = useState<string | null>(null);
  const setVideoUrl = (url: string | null) =>
    setVideoUrlState((prev) => {
      if (prev && prev !== url && prev.startsWith("blob:")) {
        URL.revokeObjectURL(prev);
      }
      return url;
    });
  useEffect(() => {
    return () => {
      if (videoUrl && videoUrl.startsWith("blob:"))
        URL.revokeObjectURL(videoUrl);
    };
  }, [videoUrl]);

  const [imageUrl, setImageUrlState] = useState<string | null>(null);
  const setImageUrl = (url: string | null) =>
    setImageUrlState((prev) => {
      if (prev && prev !== url && prev.startsWith("blob:")) {
        URL.revokeObjectURL(prev);
      }
      return url;
    });
  useEffect(() => {
    return () => {
      if (imageUrl && imageUrl.startsWith("blob:"))
        URL.revokeObjectURL(imageUrl);
    };
  }, [imageUrl]);

  // command_error banner: transient, not part of the reducer.
  const [commandError, setCommandError] = useState<string | null>(null);
  const bannerTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const showCommandError = (reason: string) => {
    if (bannerTimerRef.current) clearTimeout(bannerTimerRef.current);
    setCommandError(reason);
    bannerTimerRef.current = setTimeout(
      () => setCommandError(null),
      BANNER_TTL_MS,
    );
  };

  // Bumped on user reset; keys children holding draft state so they remount.
  const [resetNonce, setResetNonce] = useState(0);

  // Blank the stage while generation is stopped (the view would freeze).
  const [stageCleared, setStageCleared] = useState(false);

  useX2StateUpdate((msg) => {
    if (msg.generating) setStageCleared(false);
    setUi((s) => reduce(s, msg));
  });

  useX2GenerationStopped((msg) => {
    setStageCleared(true);
    // `reference_image_changed` stops restart automatically; keep drafts.
    if (msg.reason === "reset") setResetNonce((n) => n + 1);
  });

  useX2ReferenceImageAccepted((msg) => {
    setUi((s) => ({
      ...s,
      referenceAccepted: { width: msg.width, height: msg.height },
    }));
  });

  useX2CommandError((msg) => {
    showCommandError(`${msg.command}: ${msg.reason}`);
  });

  // Reset local state on disconnect.
  useEffect(() => {
    if (status === "disconnected") {
      setUi(DEFAULT_UI_STATE);
      setCommandError(null);
      setVideoUrl(null);
      setImageUrl(null);
      setStageCleared(false);
    }
  }, [status]);

  useEffect(() => {
    return () => {
      if (bannerTimerRef.current) clearTimeout(bannerTimerRef.current);
    };
  }, []);

  return (
    <div className="flex min-h-screen flex-col lg:h-screen lg:overflow-hidden">
      <Header />
      {/* Stage first in the DOM so it can be sticky on mobile;
          lg:flex-row-reverse puts the sidebar on the left on desktop. */}
      <main className="flex flex-1 flex-col lg:min-h-0 lg:flex-row-reverse lg:gap-6 lg:p-6">
        <section className="flex flex-col gap-4 max-lg:sticky max-lg:top-0 max-lg:z-10 max-lg:bg-zinc-950/95 max-lg:p-4 max-lg:pb-3 max-lg:backdrop-blur-sm lg:min-h-0 lg:min-w-0 lg:flex-1 lg:overflow-hidden">
          <Stage
            ui={ui}
            mode={mode}
            videoUrl={videoUrl}
            imageUrl={imageUrl}
            cleared={stageCleared}
            onTrack={setSourceTrack}
          />
        </section>
        <aside className="flex w-full flex-col gap-4 p-4 pt-1 lg:w-80 lg:shrink-0 lg:min-h-0 lg:overflow-y-auto lg:p-0">
          <StatusBadge />
          {commandError && (
            <CommandError
              message={commandError}
              onDismiss={() => setCommandError(null)}
            />
          )}
          {publishError && (
            <p className="text-xs text-red-400">
              Publish error: {publishError}
            </p>
          )}
          {mode === "stream" && (
            <label className="flex items-start gap-2 font-mono text-xs text-zinc-500">
              <input
                type="checkbox"
                checked={rosLoop}
                onChange={(e) => setRosLoop(e.target.checked)}
                className="mt-0.5"
              />
              <span>
                publish back to ROS
                {rosLoop && (
                  <>
                    {" - "}
                    {rosOut.publishing
                      ? `/world_model/image_raw · ${rosOut.published} frames${
                          rosOut.size ? ` · ${rosOut.size}` : ""
                        }`
                      : "waiting for model output"}
                  </>
                )}
              </span>
            </label>
          )}
          <SourcePanel
            generating={ui.generating}
            keepBacklog={ui.keepBacklog}
            mode={mode}
            onModeChange={setMode}
            onSelectVideo={(url) => setVideoUrl(url)}
            onSelectImage={(url) => setImageUrl(url)}
            onTrack={setSourceTrack}
          />
          <Prompt key={`p${resetNonce}`} activePrompt={ui.activePrompt} />
          <PointerPanel />
          <ReferenceImage
            key={`r${resetNonce}`}
            generating={ui.generating}
            hasReference={ui.hasReference}
            accepted={ui.referenceAccepted}
          />
          <SnapClip />
        </aside>
      </main>
    </div>
  );
}
