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
// A route handler returning the upstream `body` stream forwards chunks as they
// arrive, in dev and in production alike.
//
// THE QUERY STRING MUST BE REBUILT BY HAND, NOT PASSED THROUGH
// --------------------------------------------------------------
// `new URL(request.url).search` looked like the obvious way to forward the
// query string, and it silently re-broke the exact failure BUILD_SPEC and
// panel/index.html already warn about: Next's own URL parsing of the
// incoming request re-encodes '/' in the query string, so `?topic=/unity/
// camera/image_raw` arrives here as `?topic=%2Funity%2Fcamera%2Fimage_raw`.
// web_video_server does not URL-decode its query parameters, so that percent-
// encoded slash reaches it as a literal character in the topic name -- which
// matches nothing -- and it writes the multipart boundary line, finds no
// frames for a topic that does not exist, and the stream just sits there
// (from the client's point of view: 22 bytes, then a clean close). No error
// anywhere; this is what silent looks like. `searchParams.get()` decodes
// correctly, so the fix is to read `topic` through that and place it back on
// the URL RAW, exactly like the browser-side fix in panel/index.html.
const WVS = process.env.WEB_VIDEO_SERVER_URL || "http://127.0.0.1:8080";

// Never prerender or cache: this is a live stream.
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
    // ROS topic names only ever contain alphanumerics, '_', '~', '/', '{',
    // '}' -- there is nothing in a valid topic name that needs escaping, and
    // escaping it anyway is what breaks it. Every other param (type, quality,
    // default_transport, width, height, ...) is a plain token and safe to
    // encode normally.
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
      // The boundary lives in this header. Rebuilding it by hand would be a
      // silent way to break multipart parsing, so pass it through verbatim.
      "Content-Type":
        upstream.headers.get("Content-Type") || "application/octet-stream",
      "Cache-Control": "no-store, no-transform",
    },
  });
}
