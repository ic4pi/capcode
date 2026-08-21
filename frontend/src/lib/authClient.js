// Neon Managed Better Auth — browser client.
//
// Uses Neon's own `@neondatabase/auth` wrapper (NOT raw better-auth/react).
// Raw better-auth has no idea about Neon's cross-domain OAuth handoff — after
// Google redirects back, Neon appends `?neon_auth_session_verifier=...` to
// the URL, and only the Neon client knows how to consume that token to
// finalize the session. Using plain better-auth left that verifier unused,
// so sign-in silently never completed.
import { createInternalNeonAuth } from "@neondatabase/auth";

// IMPORTANT: the client must talk to a SAME-ORIGIN proxy, never Neon's raw
// auth domain directly. If it hits Neon's domain directly, the session
// cookie Neon sets is scoped to Neon's own domain — third-party relative to
// this app's domain — which Safari/iOS (and Chrome's newer third-party
// cookie phase-out) silently drops, so get-session always comes back null
// right after a successful OAuth redirect. Routing through `/api/neon-auth`
// (same-origin) makes the cookie first-party in every environment:
//  - This Emergent preview: `/api/*` is ingress-routed to the FastAPI
//    backend, which has its own `/api/neon-auth/{path}` reverse proxy.
//  - Vercel production: `/api/*` resolves to the serverless function at
//    `api/neon-auth/[...path].js`, which proxies to the same NEON_AUTH_URL.
// better-auth's client requires an absolute URL (it rejects relative paths),
// so we build one from the current origin at runtime instead of baking a
// fixed domain into the build.
const NEON_AUTH_URL =
  typeof window !== "undefined" && window.location
    ? `${window.location.origin}/api/neon-auth`
    : process.env.REACT_APP_NEON_AUTH_URL;

if (!NEON_AUTH_URL) {
  console.error(
    "REACT_APP_NEON_AUTH_URL is not set — Neon Auth cannot be used."
  );
}

// createAuthClient() only returns the adapter (signIn/signOut/getSession) —
// getJWTToken lives one level up, on the internal wrapper, so we need the
// non-public factory to get both.
const { adapter, getJWTToken } = createInternalNeonAuth(NEON_AUTH_URL);
export const authClient = adapter;

// Finalizes the OAuth session — consumes `neon_auth_session_verifier` from
// the URL (if present) and resolves the current session, or null.
export async function getSession() {
  try {
    const res = await authClient.getSession();
    return res?.data?.session ? res.data : null;
  } catch (_e) {
    return null;
  }
}

// Returns the current JWT, or null when not signed in.
export async function getJwt() {
  try {
    return (await getJWTToken()) || null;
  } catch (_e) {
    return null;
  }
}

export async function signInWithGoogle() {
  const callbackURL = window.location.origin + "/";
  return authClient.signIn.social({ provider: "google", callbackURL });
}

export async function signOut() {
  try {
    await authClient.signOut();
  } catch (_) {
    /* noop */
  }
}
