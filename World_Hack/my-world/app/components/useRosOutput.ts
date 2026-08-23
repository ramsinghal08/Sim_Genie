"use client";

import { useEffect, useRef, useState } from "react";
import { useX2, useX2Track } from "@reactor-models/x2";
import { X2Sink } from "@/app/lib/ros/bridge.js";
import {
  connectRos,
  disconnectRos,
  getRos,
  subscribeStatus,
  type RosStatus,
} from "@/app/lib/ros/connection";

// The return leg: X2's edited output goes back onto ROS, and ROS drives the
// prompt.
//
// This is what makes the browser a *bridge* rather than a viewer. Once
// /world_model/image_raw is being published from here, the rest of the rig
// carries on exactly as it did with x2_bridge_node.py -- perception segments
// it, the scorer scores it, and the control panel shows it in pane 2. None of
// them know or care that the model now runs in a tab.
//
// Mount this once, high in the tree, and gate it with `enabled`. OFF is the
// simple path: the stream source pulls MJPEG over plain HTTP, so with this off
// the page opens no websocket and publishes nothing. Turning it on is what
// puts the page back in the middle of the rig loop -- and it owns the rosbridge
// connection now, since nothing else needs one.

export interface RosOutputStats {
  publishing: boolean;
  published: number;
  size: string | null;
  lastPrompt: string | null;
}

export function useRosOutput({
  enabled = false,
  rosUrl = "ws://localhost:9090",
  outputTopic = "/world_model/image_raw",
  promptTopic = "/world_model/prompt",
  stateTopic = "/world_model/state",
  fps = 12,
  maxWidth = 960,
}: {
  enabled?: boolean;
  rosUrl?: string;
  outputTopic?: string;
  promptTopic?: string;
  stateTopic?: string;
  fps?: number;
  maxWidth?: number;
} = {}): RosOutputStats {
  const track = useX2Track("main_video");
  const { setPrompt } = useX2();
  const [rosStatus, setRosStatus] = useState<RosStatus>("disconnected");
  const [stats, setStats] = useState<RosOutputStats>({
    publishing: false, published: 0, size: null, lastPrompt: null,
  });

  const sinkRef = useRef<InstanceType<typeof X2Sink> | null>(null);

  useEffect(() => subscribeStatus(setRosStatus), []);

  // Own the connection: opened when switched on, closed when switched off, so
  // "off" really means no websocket rather than a quiet one.
  useEffect(() => {
    if (!enabled) {
      disconnectRos();
      return;
    }
    void connectRos(rosUrl);
    return () => disconnectRos();
  }, [enabled, rosUrl]);

  // ---- output track -> ROS ------------------------------------------------
  useEffect(() => {
    const ros = getRos();
    if (!enabled || !ros || !track) return;

    const sink = new X2Sink(ros, outputTopic, { fps, maxWidth });
    sinkRef.current = sink;
    sink.onPublish = (n: number, w: number, h: number) => {
      // Throttled to once a second: this fires at `fps` and a setState per
      // published frame would re-render the workspace 12 times a second.
      if (n === 1 || n % fps === 0) {
        setStats((s) => ({ ...s, publishing: true, published: n,
                           size: `${w}x${h}` }));
      }
    };
    let cancelled = false;
    sink.attach(track).catch((e: Error) => {
      if (!cancelled) console.error("[x2-ros] output sink:", e.message);
    });

    return () => {
      cancelled = true;
      sink.stop();
      sinkRef.current = null;
      setStats((s) => ({ ...s, publishing: false }));
    };
  }, [enabled, track, rosStatus, outputTopic, fps, maxWidth]);

  // ---- ROS prompt -> the model -------------------------------------------
  //
  // The control panel and prompt_director both publish here. Forwarded
  // VERBATIM: a single changed word invalidates the comparison the whole rig
  // exists to make, so the only edit is X2's own 1000-character cap.
  useEffect(() => {
    const ros = getRos();
    if (!enabled || !ros) return;

    ros.subscribe(promptTopic, "std_msgs/msg/String", (msg: { data?: string }) => {
      const text = (msg.data || "").trim();
      if (!text) return;
      const prompt = text.length > 1000 ? text.slice(0, 1000) : text;
      setStats((s) => ({ ...s, lastPrompt: prompt }));
      setPrompt({ prompt }).catch((e: Error) =>
        console.error("[x2-ros] set_prompt:", e.message));
    });

    return () => ros.unsubscribe(promptTopic);
  }, [enabled, rosStatus, promptTopic, setPrompt]);

  // ---- session state -> ROS ----------------------------------------------
  //
  // Keeps the control panel's BRIDGE card alive, and says plainly that frames
  // are coming through a browser rather than x2_bridge_node.
  useEffect(() => {
    const ros = getRos();
    if (!enabled || !ros) return;
    ros.advertise(stateTopic, "std_msgs/msg/String");
    const timer = setInterval(() => {
      const sink = sinkRef.current;
      ros.publish(stateTopic, {
        data: JSON.stringify({
          generating: !!(sink && sink.published),
          via: "browser (my-world)",
          output_frames: sink ? sink.published : 0,
          // Only once a frame exists: a fresh <canvas> is 300x150, and
          // reporting that as the model's output resolution puts a plausible,
          // entirely invented number on the panel.
          width: sink && sink.published ? sink.canvas.width : undefined,
          height: sink && sink.published ? sink.canvas.height : undefined,
        }),
      });
    }, 1000);
    return () => clearInterval(timer);
  }, [enabled, rosStatus, stateTopic]);

  return stats;
}
