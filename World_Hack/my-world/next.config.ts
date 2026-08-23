import type { NextConfig } from "next";

// web_video_server is proxied by a ROUTE HANDLER (app/api/wvs/[...path]),
// not by a rewrite here. A `next dev` rewrite buffers the response and waits
// for the body to end; an MJPEG stream never ends, so the browser received
// nothing -- silently, and only in dev. See that file for the measurements.
const nextConfig: NextConfig = {};

export default nextConfig;
