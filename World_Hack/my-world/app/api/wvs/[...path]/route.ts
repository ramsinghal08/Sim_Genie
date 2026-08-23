// A streaming proxy to web_video_server.
//
// WHY NOT A next.config REWRITE
// ----------------------------
// It was one, and it silently broke the whole ROS source. `next dev` BUFFERS a
// rewritten response: it waits for the upstream body to finish before sending
// anything. An MJPEG stream is `multipart/x-mixed-replace`, which never
// finishes, so the browser received nothing at all -- no error, no frame, no
// clue. Measured against the same server in the same four seconds:
//
//     direct to web_video_server   2,636,720 bytes
//     through the dev rewrite              0 bytes
//
// `next start` streamed it, which is exactly the trap: it worked in a
// production build and failed for everyone running `npm run dev`.
//
// A route handler returning the upstream `body` stream forwards chunks as they
// arrive, in dev and in production alike.
//
// WHY PROXY AT ALL
// ----------------
// The frames land in a <canvas> whose captureStream() feeds WebRTC, and a
// canvas that has drawn a CROSS-ORIGIN image is tainted -- a tainted canvas
// cannot be captured. web_video_server is on another port (and this app is
// often opened on a LAN address), so it is always cross-origin. Same-origin
// through here sidesteps it without depending on web_video_server's CORS.

// 127.0.0.1, not "localhost". Node resolves localhost to ::1 first on some
// systems; if web_video_server bound IPv4-only the server-side fetch fails
// while the browser -- which falls back -- reaches it fine. That asymmetry
// looks exactly like "the app is broken and the panel works".
const WVS = process.env.WEB_VIDEO_SERVER_URL || "http://127.0.0.1:8080";

// Never prerender or cache: this is a live stream.
export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET(
  request: Request,
  context: { params: Promise<{ path: string[] }> },
) {
  const { path } = await context.params;
  const search = new URL(request.url).search;
  const target = `${WVS}/${path.join("/")}${search}`;

  let upstream: Response;
  try {
    upstream = await fetch(target, {
      // The client going away must tear the upstream connection down too,
      // or every reload leaves web_video_server encoding for nobody.
      signal: request.signal,
      cache: "no-store",
    });
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
      // The boundary lives in this header. Rebuilding it by hand would be a
      // silent way to break multipart parsing, so pass it through verbatim.
      "Content-Type":
        upstream.headers.get("Content-Type") || "application/octet-stream",
      "Cache-Control": "no-store, no-transform",
    },
  });
}
