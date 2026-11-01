import { NextResponse } from "next/server";
import { REACTOR_API_URL } from "@/app/lib/config";

// The minted JWT is scoped to this model and to the sessions it creates.
const MODEL_NAME = "xmax/x2";

// Max sessions per token (closed sessions still count). The browser caches the
// token, so leave room for reconnects.
const MAX_SESSIONS = 10;

// Requested JWT lifetime (the server caps it at 6h).
const TOKEN_LIFETIME_SECONDS = 60 * 60;

// Cache-lifetime margin so a request does not race the real expiry.
const CACHE_SKEW_SECONDS = 60;

// Mints a session-scoped Reactor JWT, returned with a private Cache-Control
// header so the browser reuses it until expiry. Exposed as GET because browsers
// do not cache POST responses; `max-age` is derived from the server-granted
// `expires_at`. `authorization_details` downscopes the token to MODEL_NAME
// sessions it started itself.
export async function GET() {
  const apiKey = process.env.REACTOR_API_KEY;
  if (!apiKey) {
    return NextResponse.json(
      { error: "REACTOR_API_KEY is not set on the server" },
      { status: 500 },
    );
  }

  const res = await fetch(`${REACTOR_API_URL}/tokens`, {
    method: "POST",
    headers: {
      "Reactor-API-Key": apiKey,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      expires_after: TOKEN_LIFETIME_SECONDS,
      authorization_details: [
        {
          type: "session",
          resources: { models: { match: [MODEL_NAME] } },
          constraints: { max_sessions: MAX_SESSIONS },
        },
      ],
    }),
  });

  if (!res.ok) {
    return NextResponse.json(
      { error: `Reactor /tokens returned ${res.status}` },
      { status: 502 },
    );
  }

  const { jwt, expires_at } = (await res.json()) as {
    jwt: string;
    expires_at: number;
  };

  const nowSeconds = Math.floor(Date.now() / 1000);
  const maxAge = Math.max(0, expires_at - nowSeconds - CACHE_SKEW_SECONDS);

  return NextResponse.json(
    { jwt },
    {
      headers: {
        "Cache-Control": `private, max-age=${maxAge}`,
      },
    },
  );
}
