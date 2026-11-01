"use client";

import { useEffect, useRef, useState } from "react";
import { useX2 } from "@reactor-models/x2";
import { PRESET_IMAGES } from "@/app/lib/clips";
import { Panel, cn, EYEBROW, FOCUS_RING, errorMessage } from "./ui";

// The runtime scrambles color channels when a streamed frame's width or height
// is not a multiple of 4. Center-crop (at most 3px per side) to the nearest
// multiple of 4 before upload. Best-effort: on failure the original file is
// returned.
async function toMod4(file: File): Promise<Blob> {
  const url = URL.createObjectURL(file);
  try {
    const img = await new Promise<HTMLImageElement>((resolve, reject) => {
      const image = new Image();
      image.onload = () => resolve(image);
      image.onerror = () => reject(new Error("decode failed"));
      image.src = url;
    });
    const w = Math.max(4, Math.floor(img.naturalWidth / 4) * 4);
    const h = Math.max(4, Math.floor(img.naturalHeight / 4) * 4);
    if (w === img.naturalWidth && h === img.naturalHeight) return file;
    const canvas = document.createElement("canvas");
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext("2d");
    if (!ctx) return file;
    ctx.drawImage(
      img,
      (img.naturalWidth - w) / 2,
      (img.naturalHeight - h) / 2,
      w,
      h,
      0,
      0,
      w,
      h,
    );
    const blob = await new Promise<Blob | null>((resolve) =>
      canvas.toBlob(resolve, "image/png"),
    );
    return blob ?? file;
  } catch {
    return file;
  } finally {
    URL.revokeObjectURL(url);
  }
}

// Reference-image panel for character/object insertion or swap. Uploads the
// image via uploadFile, then sends set_reference_image with the FileRef. Setting
// it mid-run restarts the stream automatically. The parent keys this component
// on the reset nonce so a user reset drops the local preview.
export function ReferenceImage({
  generating,
  hasReference,
  accepted,
}: {
  generating: boolean;
  hasReference: boolean;
  accepted: { width: number; height: number } | null;
}) {
  const { uploadFile, setReferenceImage, status } = useX2();
  const inputRef = useRef<HTMLInputElement>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const ready = status === "ready";

  useEffect(() => {
    return () => {
      if (previewUrl) URL.revokeObjectURL(previewUrl);
    };
  }, [previewUrl]);

  async function apply(file: File) {
    if (busy || !ready) return;
    setBusy(true);
    setError(null);
    try {
      // Normalize to mod-4 dimensions first (see toMod4).
      const ref = await uploadFile(await toMod4(file), { name: file.name });
      await setReferenceImage({ reference_image: ref });
      setPreviewUrl((prev) => {
        if (prev) URL.revokeObjectURL(prev);
        return URL.createObjectURL(file);
      });
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function pick(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (inputRef.current) inputRef.current.value = "";
    if (file) await apply(file);
  }

  // Fetch a demo preset into a File so it uses the same upload path.
  async function pickPreset(preset: (typeof PRESET_IMAGES)[number]) {
    if (busy || !ready) return;
    setError(null);
    try {
      const blob = await (await fetch(preset.src)).blob();
      await apply(new File([blob], `${preset.id}.jpg`, { type: "image/jpeg" }));
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  return (
    <Panel label="Reference image">
      <div className="mb-2 flex flex-wrap gap-1.5">
        {PRESET_IMAGES.map((preset) => (
          <button
            key={preset.id}
            type="button"
            disabled={!ready || busy}
            onClick={() => void pickPreset(preset)}
            className={cn(
              "rounded-md border border-zinc-700 p-1 transition hover:border-zinc-500 disabled:opacity-40 disabled:pointer-events-none",
              FOCUS_RING,
            )}
            aria-label={`Use demo reference: ${preset.label}`}
            title={preset.label}
          >
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={preset.src}
              alt={preset.label}
              className="h-10 w-10 rounded object-cover"
            />
          </button>
        ))}
      </div>
      <label
        className={cn(
          "flex cursor-pointer items-center gap-3 rounded-md border border-dashed border-zinc-700 p-3 transition hover:border-zinc-500",
          (!ready || busy) && "pointer-events-none opacity-40",
        )}
      >
        <input
          ref={inputRef}
          type="file"
          accept="image/*"
          onChange={(e) => void pick(e)}
          className="hidden"
        />
        {previewUrl ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={previewUrl}
            alt="Reference"
            className="h-14 w-14 shrink-0 rounded border border-zinc-800 object-cover"
          />
        ) : (
          <span className="flex h-14 w-14 shrink-0 items-center justify-center rounded border border-zinc-800 text-lg text-zinc-600">
            +
          </span>
        )}
        <span className="min-w-0">
          <span className={EYEBROW}>
            {busy
              ? "uploading…"
              : previewUrl
                ? "replace image"
                : "add an image"}
          </span>
          <span className="mt-0.5 block text-xs text-zinc-500">
            Character or object to insert / swap in.
          </span>
        </span>
      </label>

      {error && <p className="mt-2 text-xs text-red-400">{error}</p>}

      {accepted && !error && hasReference && (
        <p className="mt-2 text-xs text-zinc-500">
          Accepted ({accepted.width}×{accepted.height}).{" "}
          {generating
            ? "Conditioning the current run - replacing it restarts the stream."
            : "Will condition the next run."}
        </p>
      )}
    </Panel>
  );
}
