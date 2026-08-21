# CapCode — PRD / Architecture Memory

## Original problem statement
Recursive AI dev-team app (CapCode): 6-role pipeline Teacher -> Architect ->
Artist -> Reviewer -> Executor/Corrector -> Rater -> Scorer. Checkpointed
pipeline, Tier System (free/trial/paid), Uncensored Mode waiver (Venice).

## Stack
- Backend: FastAPI + Neon Postgres via `docdb.py` (JSONB adapter, Mongo-like API)
- Frontend: React + Tailwind, BBS/neon terminal aesthetic
- Auth: Neon Managed Better Auth (Google OAuth), proxied through backend
  `/api/neon-auth/*` to fix Safari cross-domain cookie issues
- Deploy: Frontend on Vercel, Backend on Render. User deploys via zip download
  (Emergent->GitHub integration currently broken on their end)
- Billing: Stripe — $16/mo (or annual) subscription, $2.99 one-time trial,
  now also one-time credit top-ups ($8/500, $16/1000, $32/2000 credits)

## Implemented (this session, Aug 2026)
1. **Dead Vercel code cleanup** — deleted `/app/api/index.py` + root
   `requirements.txt`; removed the `functions` block + `/api/(.*)` rewrite
   from `vercel.json` (frontend calls Render directly via
   `REACT_APP_BACKEND_URL`, so this was pure dead weight/build risk).
2. **Default LLM Teams** — `providers.DEFAULT_TEAMS` (venice/openrouter/nvidia
   presets, all 6 roles). `GET /api/teams/defaults`, `POST /api/teams/reset`.
   Settings extended from 3 roles (teacher/artist/rater) to all 6
   (+architect/reviewer/corrector) — `SETTINGS_ROLES` in server.py.
   Frontend: SettingsPanel "DEFAULT TEAMS" section, 6 role tabs, Venice preset
   gates through the existing consent waiver modal first.
3. **Credit top-ups** — `billing.TOPUP_PACKAGES` ($8→500cr, $16→1000cr,
   $32→2000cr), `add_topup_credits`/`get_topup_balance`, persistent
   `users.topup_credits_balance` (doesn't expire on cycle renewal; spent
   before card overage in `apply_chain_billing`). Endpoints:
   `GET /api/tier/topup/packages`, `POST /api/tier/topup/checkout`.
   **REAL Stripe wired (Aug 2026)**: provisioned an Emergent-managed
   claimable Stripe test sandbox (`acct_1U6LKqRT8QV9RDE8`) and created all 6
   real Price objects (trial/paid/paid_annual/credits_8/16/32) in it —
   `STRIPE_SECRET_KEY`, `STRIPE_PUBLISHABLE_KEY`, `STRIPE_WEBHOOK_SECRET`,
   and all `STRIPE_PRICE_ID_*` vars are now live in `backend/.env`,
   `ALLOW_MOCK_BILLING=false`. Webhook endpoint re-pointed to the app's real
   route `/api/tier/webhook` (Stripe's dashboard defaulted it to
   `/api/stripe/webhook`, which doesn't exist in this app). Verified all 6
   checkout sessions create real Stripe Checkout URLs. Tax mode: **DIY**
   (Stripe just processes payment, no tax calc/filing) — matches the
   existing checkout code which never enabled automatic_tax. Can switch to
   Stripe-calculates-only or full managed tax later on request.
   **To go live in production (Render)**: claim this sandbox via
   `https://dashboard.stripe.com/onboard_sandbox/YWNjdF8xVTZMS3FSVDhRVjlSREU4LDE3ODc4MDU0NjEv100m7xhtfBX`
   (completes Stripe KYC on the user's real business), then copy the same
   6 env vars from this pod's `backend/.env` into Render's env vars. Live
   keys auto-activate once KYC clears — no code changes needed.
4. **User Profile UI** — `GET /api/profile` (email, tier, trial days left,
   billing cycle, topup balance). New `ProfilePanel.jsx`, "profile" button in
   App.js header (only shown when signed in).
5. Legal docs — real Privacy Policy + Terms of Service pages added
   (`LegalPage.jsx`), linked from Landing/Pricing/build-view footers. Distinct
   from `VeniceConsentModal.jsx` (that's the narrow uncensored-mode-only
   liability waiver, unchanged). Entity name "CapCode", no dedicated legal
   contact email yet, governing-law kept generic per user's call.
6. Fixed layout bug from testing: SettingsPanel role-tab row was flex-shrunk
   to ~5px by the new Default Teams section — added `shrink-0` to all fixed
   sections + `min-h-0` on the model-list scroll region. Also fixed a bug
   where applying the Venice preset also silently force-enabled the
   unrelated "uncensored only" catalog filter checkbox.

## Testing status
- `/app/test_reports/iteration_13.json` — backend 21/21 pass, frontend
  90%→100% after layout fix (retest recommended but fix is a pure CSS change
  mirroring the exact fix the testing agent RCA'd).
- Known limitation: no email/password UI exists, only Google OAuth — so
  signed-in-only flows (profile contents, topup checkout mock-credit,
  real billing) can only be smoke-tested for correct 401s in this env, not
  fully e2e. Anonymous-session flows (teams reset, 6-role settings) are
  fully tested and pass.

## Pre-existing (not touched this session, noted by testing agent)
- `POST /api/teams/reset` (like the pre-existing `/api/settings`) trusts a
  caller-supplied `session_id` in the body rather than deriving it from the
  `X-Capcode-Session` header — this is the SAME pattern the existing
  `/api/settings` GET/POST already use, not a new regression. Would need a
  broader auth-flow change to fix consistently; out of scope for this session.

## Deployment reality check (Aug 2026)
This preview sandbox is where I build/test everything. The user's LIVE app is
on Vercel (frontend) + Render (backend), deployed by manually downloading a
zip (their GitHub push integration is broken). **Nothing I build here reaches
their live site until they redeploy that zip.** Confirmed via deployment_agent
scan (Aug 2026): no code blockers, CORS/`.gitignore`/auth-redirect all pass.
Live-site "sign in / upgrade broken, no profile" reports are most likely stale
code, not a new regression — pending user confirmation of exact live error +
whether `STRIPE_SECRET_KEY` is actually set in Render's env vars.

## Auth bug — ROOT CAUSE FOUND + FIXED (Aug 2026, this session)
Two independent real bugs were causing "sign-in silently fails / bounces to
logged-out state", found by reproduction (not guesswork):
1. **`authClient.js` never used the proxy at all.** Despite the backend
   `/api/neon-auth/{path}` reverse proxy and the Vercel serverless proxy
   `api/neon-auth/[...path].js` both existing in the repo, the browser client
   was still built with `createInternalNeonAuth(process.env.REACT_APP_NEON_AUTH_URL)`
   — the RAW cross-domain Neon URL — bypassing both proxies entirely. That
   makes Neon's session cookie third-party to the app's own domain, which
   Safari/ITP (and increasingly Chrome) blocks, so `get-session` always came
   back `null` right after a real OAuth login. **Fix**: `authClient.js` now
   builds the URL as `${window.location.origin}/api/neon-auth` at runtime —
   always same-origin, in every environment (this preview's ingress routes
   `/api/*` to the FastAPI proxy; Vercel resolves it to the serverless
   function). Verified end-to-end via curl: sign-up → cookie set host-only
   (no `Domain=` attr, confirmed via a real Neon response) → `get-session`
   returns the real session → `/token` mints a real JWT — full loop works
   through the proxy.
2. **Backend JWT verification always failed with `InvalidAudienceError`.**
   `_verify_neon_jwt` (server.py) called `pyjwt.decode()` without an
   `audience` param. PyJWT rejects any token that has an `aud` claim unless
   you explicitly pass the expected audience — Neon's tokens always have
   `aud` = the auth server's bare origin (`scheme://host`, NOT the full
   `.../neondb/auth` path — confirmed by decoding a real signed token). So
   even a perfectly valid session/JWT would 401 at `/api/auth/me`, which is
   exactly "logs in then bounces back to signed-out" from the user's POV.
   **Fix**: added `_NEON_AUTH_ORIGIN` (bare origin parsed from `NEON_AUTH_URL`)
   and pass it as `audience=` in the decode call. Verified: minted a real JWT
   via the proxy, `/api/auth/me` and `/api/profile` both now correctly return
   the signed-in user (previously always "not authenticated").
3. Added a startup print in `usage.py` (`groq key pools loaded: free=N
   trial=N`) so Render logs make it instantly visible whether the Groq key
   env vars were actually picked up after a redeploy — was previously
   silent, making that recurring issue hard to confirm either way.

**Could not fully click-through test via a real browser OAuth redirect in
this preview**: Neon's trusted-origins allowlist only includes the real
production domain (`capcode-mu.vercel.app`, confirmed accepted when spoofing
that Origin directly against Neon) — this ephemeral preview domain isn't
registered there (expected; it changes every fork) so `sign-up`/`sign-in`
calls from an actual browser tab on this preview URL correctly get rejected
with `INVALID_ORIGIN` by Neon itself, not by our code. This is why testing
was done via curl + a real minted JWT instead of a live Google OAuth click.
**Both fixes are code-only (`authClient.js`, `server.py`, `usage.py`) and
must be redeployed (GitHub push → Vercel + Render) to take effect on the
live site — nothing changes on the live site until that happens.**

Regression check: existing `tests/test_auth.py` / `test_ownership.py`
failures using a hardcoded `"MY_TEST_TOKEN"` string are pre-existing and
unrelated (confirmed via `git stash` — identical failures before my changes;
those tests predate the Neon Auth migration and were never passing).

## Backlog (P1/P2/P3)
- P2: Optionally split SettingsPanel.jsx (now ~530 lines) into smaller
  components (billing / teams / roles / BYOK) — code-health only, not
  user-facing.
- P3: Layer #2 Test Generation Loop, #3 Error Pattern Library,
  #4 Architecture Decisions loop, #6 Dependency Selection loop.
