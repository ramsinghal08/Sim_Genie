import { X2App } from "./X2App";
import { SetupRequired } from "./SetupRequired";

// Server Component: renders the setup screen when REACTOR_API_KEY is missing,
// otherwise the app (which fetches its JWT from /api/reactor/token).
// force-dynamic runs the env check per request.
export const dynamic = "force-dynamic";

export default function Page() {
  const hasKey = !!process.env.REACTOR_API_KEY;
  return hasKey ? <X2App /> : <SetupRequired />;
}
