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

// Return leg: publishes X2's output to ROS and forwards ROS prompts to the
// model. Mount once, high in the tree, gated by `enabled`. When off, no
// websocket is opened and nothing is published; this hook owns the rosbridge
// connection.

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

  // Connection is open only while enabled.
  useEffect(() => {
    if (!enabled) {
      disconnectRos();
      return;
    }
    void connectRos(rosUrl);
    return () => disconnectRos();
  }, [enabled, rosUrl]);

  // Output track -> ROS.
  useEffect(() => {
    const ros = getRos();
    if (!enabled || !ros || !track) return;

    const sink = new X2Sink(ros, outputTopic, { fps, maxWidth });
    sinkRef.current = sink;
    sink.onPublish = (n: number, w: number, h: number) => {
      // Throttle to ~1/s to avoid re-rendering on every frame.
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

  // ROS prompt -> model. Forwarded verbatim except for X2's 1000-char cap.
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

  // Session state -> ROS (control panel BRIDGE card).
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
          // Omitted until a frame exists (a fresh canvas is 300x150).
          width: sink && sink.published ? sink.canvas.width : undefined,
          height: sink && sink.published ? sink.canvas.height : undefined,
        }),
      });
    }, 1000);
    return () => clearInterval(timer);
  }, [enabled, rosStatus, stateTopic]);

  return stats;
}
