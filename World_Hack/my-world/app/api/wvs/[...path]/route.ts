// Streaming proxy to web_video_server. A route handler is used instead of a
// next.config rewrite because `next dev` buffers rewritten responses, and an
// MJPEG (multipart/x-mixed-replace) stream never finishes.
//
// The query string is rebuilt by hand: `topic` is forwarded unencoded because
// web_video_server does not URL-decode it, so a percent-encoded '/' would match
// no topic.
const WVS = process.env.WEB_VIDEO_SERVER_URL || "http://127.0.0.1:8080";

// Live stream: never prerender or cache.
export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET(
  request: Request,
  context: { params: Promise<{ path: string[] }> },
) {
  const { path } = await context.params;
  const incoming = new URL(request.url);
  const parts: string[] = [];
  for (const [key, value] of incoming.searchParams) {
    // Valid ROS topic names need no escaping; other params are encoded normally.
    parts.push(
      key === "topic" ? `topic=${value}` : `${encodeURIComponent(key)}=${encodeURIComponent(value)}`,
    );
  }
  const target =
    `${WVS}/${path.join("/")}` + (parts.length ? `?${parts.join("&")}` : "");

  let upstream: Response;
  try {
    upstream = await fetch(target, { cache: "no-store" });
  } catch (e) {
    return new Response(
      `cannot reach web_video_server at ${WVS}: ${
        e instanceof Error ? e.message : String(e)
      }`,
      { status: 502 },
    );
  }

  return new Response(upstream.body, {
    status: upstream.status,
    headers: {
      // Passed through verbatim: it carries the multipart boundary.
      "Content-Type":
        upstream.headers.get("Content-Type") || "application/octet-stream",
      "Cache-Control": "no-store, no-transform",
    },
  });
}
