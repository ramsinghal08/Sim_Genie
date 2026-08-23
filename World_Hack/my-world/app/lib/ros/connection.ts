"use client";

// One rosbridge socket for the whole page.
//
// Three things need it -- the camera source, the output sink and the prompt
// forwarder -- and they mount independently. A connection per consumer would
// mean three sockets, three reconnects and three chances to be half-connected;
// a module-level singleton is the honest shape for "this page IS the bridge".
//
// Status is a tiny pub/sub rather than context so the ROS pieces can be
// dropped into the scaffolded app without rewiring its provider tree.

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

/** The live connection, or null. Consumers must tolerate null: they mount
 *  before anyone has connected, and stay mounted after a disconnect. */
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
  // A socket that closes later must not leave the UI claiming "connected" --
  // that is the state where every pane looks fine and nothing is flowing.
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
