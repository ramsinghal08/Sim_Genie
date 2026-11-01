"use client";

import { useEffect, useRef, useState } from "react";
import { useX2 } from "@reactor-models/x2";
import { errorMessage } from "./ui";

// Single owner of the `source` slot: input modes only produce a
// MediaStreamTrack and hand it here, so mode switches cannot race two
// publishers. Reconciliation is serialized in one loop. Switching tracks
// re-publishes directly (the SDK replaces the track on the existing sender
// without renegotiating); unpublish happens only when the producer goes away
// or on unmount.
export function useSourcePublisher(
  track: MediaStreamTrack | null,
): string | null {
  const { publish, unpublish, status } = useX2();
  const [error, setError] = useState<string | null>(null);

  // Track the producer currently wants published.
  const desiredRef = useRef<MediaStreamTrack | null>(null);
  // Track believed to be published; cleared when status leaves "ready".
  const publishedRef = useRef<MediaStreamTrack | null>(null);
  // True while a reconcile loop is running (single writer).
  const busyRef = useRef(false);
  const mountedRef = useRef(true);

  useEffect(() => {
    desiredRef.current = track;
    if (status !== "ready") {
      publishedRef.current = null;
      return;
    }

    const reconcile = async () => {
      if (busyRef.current) return;
      busyRef.current = true;
      try {
        // Re-reads desiredRef each pass so mid-publish switches are picked up.
        while (
          mountedRef.current &&
          desiredRef.current !== publishedRef.current
        ) {
          const want = desiredRef.current;
          try {
            if (want) {
              await publish("source", want);
            } else {
              await unpublish("source");
            }
            publishedRef.current = want;
            setError(null);
          } catch (e) {
            // Stop; the next track/status change retries.
            setError(errorMessage(e));
            break;
          }
        }
      } finally {
        busyRef.current = false;
      }
    };
    void reconcile();
  }, [track, status, publish, unpublish]);

  // Mount flag for the reconcile loop. Deliberately mount-once: depending on
  // `unpublish` (identity changes when the store is recreated) would stop
  // publishing mid-session.
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  // Free the slot on unmount or when the store is rebuilt.
  useEffect(
    () => () => {
      if (publishedRef.current) {
        publishedRef.current = null;
        void unpublish("source").catch(() => {});
      }
    },
    [unpublish],
  );

  return error;
}
