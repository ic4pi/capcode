import axios from "axios";
import { getJwt } from "@/lib/authClient";

// No REACT_APP_BACKEND_URL -> same-origin relative /api (the correct
// default now that the backend runs natively on this Vercel deployment,
// not a separate Render service). Must default to "", not leave this
// undefined -- `${undefined}/api` template-literals to the literal string
// "undefined/api", a broken relative path, not an absolute /api root.
const BACKEND_URL = process.env.REACT_APP_BACKEND_URL || "";
export const API = `${BACKEND_URL}/api`;

// Anonymous session id — created once per browser, persisted in localStorage.
// Sent on every /api/* request as `X-Capcode-Session` so the backend can
// scope reads/writes to this browser's chains.
function _getAnonSessionId() {
  const KEY = "capcode.session_id";
  if (typeof window === "undefined") return "";
  let s = window.localStorage.getItem(KEY);
  if (!s) {
    s = (typeof crypto !== "undefined" && crypto.randomUUID)
      ? crypto.randomUUID()
      : `s-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    window.localStorage.setItem(KEY, s);
  }
  return s;
}

// Attach the anon session header + Neon Auth JWT bearer + credentials to
// every /api/* request globally.
axios.interceptors.request.use(async (config) => {
  const url = config.url || "";
  if (url.startsWith(API) || url.includes("/api/")) {
    config.headers = config.headers || {};
    config.headers["X-Capcode-Session"] = _getAnonSessionId();
    // Neon Better Auth JWT (only present when the user is signed in).
    try {
      const jwt = await getJwt();
      if (jwt) config.headers["Authorization"] = `Bearer ${jwt}`;
    } catch (_) { /* noop */ }
    config.withCredentials = true;
  }
  return config;
});

export const api = {
  status: () => axios.get(`${API}/status`).then((r) => r.data),
  evolve: (target_prompt, session_id = undefined) =>
    axios
      .post(`${API}/evolve`, { target_prompt, depth: 1, session_id }, { timeout: 30000 })
      .then((r) => r.data),
  listChains: () => axios.get(`${API}/chains`).then((r) => r.data),
  getChain: (id) => axios.get(`${API}/chains/${id}`).then((r) => r.data),
  advanceChain: (id) => axios.post(`${API}/chains/${id}/advance`).then((r) => r.data),
  verifyChain: (id) => axios.post(`${API}/chains/${id}/verify`).then((r) => r.data),
  pushChain: (id, repo, isPrivate = true) =>
    axios.post(`${API}/chains/${id}/push`, { repo, private: isPrivate }).then((r) => r.data),
  // ---- Neon Managed Better Auth ----
  authMe: () => axios.get(`${API}/auth/me`).then((r) => r.data),
  authLogout: () => axios.post(`${API}/auth/logout`).then((r) => r.data),
  authConfig: () => axios.get(`${API}/auth/config`).then((r) => r.data),
  // ---- Venice / uncensored consent waiver ----
  veniceConsent: (chain_id = null) =>
    axios.post(`${API}/venice/consent`, { chain_id }).then((r) => r.data),
  // ---- $16/mo credit subscription / $2.99 one-time trial / annual ----
  billingStatus: () => axios.get(`${API}/billing/status`).then((r) => r.data),
  checkout: (plan = "paid") => axios.post(`${API}/tier/checkout`, { plan }).then((r) => r.data),
  startTrial: () => axios.post(`${API}/tier/start-trial`).then((r) => r.data),
  tierStatus: () => axios.get(`${API}/tier/status`).then((r) => r.data),
  tierPrices: () => axios.get(`${API}/tier/prices`).then((r) => r.data),
  // ---- Edit-Diffing loop (#7) ----
  checkEditsGit: (chainId) => axios.post(`${API}/chains/${chainId}/check-edits`).then((r) => r.data),
  checkEditsUpload: (chainId, file) => {
    const form = new FormData();
    form.append("file", file);
    return axios.post(`${API}/chains/${chainId}/check-edits`, form).then((r) => r.data);
  },
  getEditDiffs: (chainId) => axios.get(`${API}/chains/${chainId}/edit-diffs`).then((r) => r.data),
  // ---- Evaluation-criteria meta-loop (#5) ----
  getEvalWeights: () => axios.get(`${API}/eval-weights`).then((r) => r.data),
  // ---- Profile + default LLM teams + credit top-ups ----
  getProfile: () => axios.get(`${API}/profile`).then((r) => r.data),
  getDefaultTeams: () => axios.get(`${API}/teams/defaults`).then((r) => r.data),
  resetTeam: (team, session_id) => axios.post(`${API}/teams/reset`, { team, session_id }).then((r) => r.data),
  topupPackages: () => axios.get(`${API}/tier/topup/packages`).then((r) => r.data),
  topupCheckout: (pkg) => axios.post(`${API}/tier/topup/checkout`, { package: pkg }).then((r) => r.data),
};
