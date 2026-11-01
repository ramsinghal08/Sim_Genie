// Reactor API URL, shared by the browser provider and the server-side token
// mint (a JWT is only valid for the environment that minted it). Override with
// NEXT_PUBLIC_REACTOR_API_URL.
export const REACTOR_API_URL =
  process.env.NEXT_PUBLIC_REACTOR_API_URL || "https://api.reactor.inc";
