"use client";

// Single shared rosbridge socket, since the camera source, output sink and
// prompt forwarder mount independently. Status is a small pub/sub rather than
// context so these pieces need no provider wiring.

import { Rosbridge } from "./rosbridge.js";

export type RosStatus =
  | "disconnected"
  | "connecting"
  | "connected"
  | { error: string };

let conn: Rosbridge | null = null;
let currentUrl = "";
let status: RosStatus = "disconnected";
const listeners = new Set<(s: RosStatus) => void>();

function setStatus(s: RosStatus) {
  status = s;
  listeners.forEach((fn) => fn(s));
}

export function getStatus(): RosStatus {
  return status;
}

export function subscribeStatus(fn: (s: RosStatus) => void): () => void {
  listeners.add(fn);
  fn(status);
  return () => listeners.delete(fn);
}

/** The live connection, or null (consumers mount before connecting). */
export function getRos(): Rosbridge | null {
  return conn && conn.ready ? conn : null;
}

export async function connectRos(url: string): Promise<void> {
  if (conn && currentUrl === url && conn.ready) return;
  disconnectRos();
  currentUrl = url;
  setStatus("connecting");
  const next = new Rosbridge(url);
  try {
    await next.connect();
  } catch (e) {
    setStatus({ error: e instanceof Error ? e.message : String(e) });
    return;
  }
  conn = next;
  // Reflect later socket closes in the status.
  next.onStatus = (s: string) => {
    if (s === "disconnected") {
      if (conn === next) conn = null;
      setStatus("disconnected");
    }
  };
  setStatus("connected");
}

export function disconnectRos(): void {
  if (conn) {
    conn.close();
    conn = null;
  }
  setStatus("disconnected");
}
