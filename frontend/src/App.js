import React, { useCallback, useEffect, useRef, useState } from "react";
import "@/App.css";
import { Toaster, toast } from "sonner";
import { LogIn, LogOut, Settings, User } from "lucide-react";
import { api, API } from "@/lib/api";
import { signInWithGoogle, signOut as neonSignOut, getJwt, getSession } from "@/lib/authClient";
import { TerminalHero } from "@/components/TerminalHero";
import { EvolveButton } from "@/components/EvolveButton";
import { ChainViewer } from "@/components/ChainViewer";
import { SettingsPanel } from "@/components/SettingsPanel";
import { ProfilePanel } from "@/components/ProfilePanel";
import { LandingPage } from "@/components/LandingPage";
import { PricingPage } from "@/components/PricingPage";
import { LegalPage } from "@/components/LegalPage";
import { AmbientBackground } from "@/components/AmbientBackground";

function getSessionId() {
  const KEY = "capcode.session_id";
  let s = localStorage.getItem(KEY);
  if (!s) {
    s = (typeof crypto !== "undefined" && crypto.randomUUID)
      ? crypto.randomUUID()
      : `s-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    localStorage.setItem(KEY, s);
  }
  return s;
}

function App() {
  const [sessionId] = useState(getSessionId);
  const [status, setStatus] = useState(null);
  const [chains, setChains] = useState([]);
  const [chain, setChain] = useState(null);
  const [busy, setBusy] = useState(false);
  const [stage, setStage] = useState(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [profileOpen, setProfileOpen] = useState(false);
  const [assignments, setAssignments] = useState(null);
  const [historyFilter, setHistoryFilter] = useState("all"); // "all" | "verified"
  const [streamBuf, setStreamBuf] = useState({ teacher: "", artist: "" });
  const [advanceLog, setAdvanceLog] = useState([]);
  const esRef = useRef(null);
  const [user, setUser] = useState(null);          // logged-in AuthUser or null
  const [checkingAuth, setCheckingAuth] = useState(true);
  const [view, setView] = useState("landing");      // "landing" | "build" | "pricing" | "privacy" | "terms"
  const [pricingReturnView, setPricingReturnView] = useState("landing");
  const [legalReturnView, setLegalReturnView] = useState("landing");

  const openPricing = (from) => { setPricingReturnView(from); setView("pricing"); };
  const openLegal = (doc, from) => { setLegalReturnView(from); setView(doc); };

  // Watch Neon Better Auth session state — fires on sign-in/out & OAuth
  // redirect back from Google.
  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        // getSession() consumes Neon's `neon_auth_session_verifier` URL param
        // (present right after a Google OAuth redirect) and finalizes the
        // session. Must run before getJwt() or the verifier is left unused
        // and the user silently stays signed out.
        await getSession();
        const returningFromOAuth = window.location.search.includes("neon_auth_session_verifier");
        if (returningFromOAuth) {
          const url = new URL(window.location.href);
          url.searchParams.delete("neon_auth_session_verifier");
          window.history.replaceState({}, document.title, url.toString());
          if (alive) {
            const preAuthView = sessionStorage.getItem("capcode.pre_auth_view");
            if (preAuthView) {
              sessionStorage.removeItem("capcode.pre_auth_view");
              setView(preAuthView);
            }
          }
        }
        // Skip the backend round-trip entirely when there's no Neon Auth JWT
        // yet — anonymous visitors would otherwise 401 against /api/auth/me
        // on every mount + every 60s poll, for nothing but console noise.
        const jwt = await getJwt();
        if (!jwt) {
          if (alive) setUser(null);
          return;
        }
        const me = await api.authMe();
        if (alive) setUser(me);
      } catch (_) {
        if (alive) setUser(null);
      } finally {
        if (alive) setCheckingAuth(false);
      }
    };
    load();
    // Re-check auth every 60s to keep the JWT fresh (Neon JWTs expire in 15m
    // but authClient.token() auto-refreshes from the session cookie).
    const iv = setInterval(load, 60_000);
    return () => { alive = false; clearInterval(iv); };
  }, []);

  const handleSignIn = async () => {
    try {
      sessionStorage.setItem("capcode.pre_auth_view", view);
      await signInWithGoogle();
    }
    catch (e) { toast.error(e?.message || "sign-in failed"); }
  };

  const handleSignOut = async () => {
    try { await neonSignOut(); } catch (_) { /* noop */ }
    try { await api.authLogout(); } catch (_) { /* noop */ }
    setUser(null);
    toast.success("SIGNED OUT");
  };

  const refresh = useCallback(async () => {
    try {
      const [s, c] = await Promise.all([api.status(), api.listChains()]);
      setStatus(s);
      setChains(c);
    } catch (e) {
      console.error(e);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  useEffect(() => {
    const handler = () => { setSettingsOpen(false); openPricing("build"); };
    window.addEventListener("capcode:open-pricing", handler);
    return () => window.removeEventListener("capcode:open-pricing", handler);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleEvolve = async (targetPrompt) => {
    setBusy(true);
    setStage("kicking off…");
    setStreamBuf({ teacher: "", artist: "" });
    setAdvanceLog([]);
    let stub = null;
    try {
      stub = await api.evolve(targetPrompt, sessionId);
      setChain(stub);
      // Open SSE stream so we can render Teacher/Artist tokens live while
      // the advance loop below drives each stage forward.
      try { esRef.current && esRef.current.close(); } catch { /* noop */ }
      const anonSid = (typeof window !== "undefined" && localStorage.getItem("capcode.session_id")) || "";
      const streamUrl = `${API}/chains/${stub.id}/stream?session_id=${encodeURIComponent(anonSid)}`;
      const es = new EventSource(streamUrl, { withCredentials: true });
      esRef.current = es;
      es.addEventListener("teacher_delta", (ev) => {
        setStreamBuf((b) => ({ ...b, teacher: b.teacher + ev.data + (ev.data.endsWith(" ") ? "" : "") }));
      });
      es.addEventListener("artist_delta", (ev) => {
        setStreamBuf((b) => ({ ...b, artist: b.artist + ev.data }));
      });
      es.onerror = () => { try { es.close(); } catch { /* noop */ } };

      // CHECKPOINTED PIPELINE — /evolve only initializes state. Each stage
      // runs on its own /advance call; we keep calling it (surfacing the
      // human-readable report each time) until the backend says done.
      let done = false;
      let guard = 0;
      while (!done && guard < 40) {
        guard += 1;
        let resp;
        try {
          resp = await api.advanceChain(stub.id);
        } catch (e) {
          if (e?.response?.status === 429) {
            await new Promise((r) => setTimeout(r, 3000));
            continue; // capacity freed up — retry the same stage
          }
          throw e;
        }
        setStage(resp.stage);
        setAdvanceLog((log) => [...log, { stage: resp.stage, report: resp.report }]);
        done = resp.done;
      }
      try { es.close(); } catch { /* noop */ }
      const finalChain = await api.getChain(stub.id);
      setChain(finalChain);
      toast.success("PRODUCT BUILT");
      refresh();
    } catch (e) {
      console.error(e);
      const msg = e?.response?.data?.detail || e?.message || "build failed";
      toast.error(msg);
      if (stub?.id) {
        try { const c = await api.getChain(stub.id); setChain(c); } catch { /* noop */ }
      }
      refresh();
    } finally {
      try { esRef.current && esRef.current.close(); } catch { /* noop */ }
      esRef.current = null;
      setBusy(false);
      setStage(null);
    }
  };

  const openChain = async (id) => {
    try { const c = await api.getChain(id); setChain(c); } catch (e) { console.error(e); }
  };

  const handleVerify = async (id) => {
    try {
      await api.verifyChain(id);
      toast.success("VERIFIED :: ADDED TO TEACHER EXEMPLARS");
      const c = await api.getChain(id);
      setChain(c);
      refresh();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "verify failed");
    }
  };

  const handlePush = async (id, repo, isPrivate) => {
    try {
      const r = await api.pushChain(id, repo, isPrivate);
      toast.success(`PUSHED :: ${r.repo}`);
      window.open(r.url, "_blank", "noopener");
      return r;
    } catch (e) {
      toast.error(e?.response?.data?.detail || "push failed");
      throw e;
    }
  };

  if (view === "landing") {
    return (
      <div className="App relative" data-testid="app-root">
        <Toaster position="bottom-right" toastOptions={{
          style: {
            background: "#15171D", border: "1px solid #2A2E38",
            color: "#EDEDF0", borderRadius: "8px",
            fontFamily: "'IBM Plex Mono', monospace", fontSize: "13px",
          },
        }} />
        <LandingPage onStart={() => setView("build")} onPricing={() => openPricing("landing")} onLegal={(doc) => openLegal(doc, "landing")} />
      </div>
    );
  }

  if (view === "privacy" || view === "terms") {
    return (
      <div className="App relative" data-testid="app-root">
        <Toaster position="bottom-right" toastOptions={{
          style: {
            background: "#15171D", border: "1px solid #2A2E38",
            color: "#EDEDF0", borderRadius: "8px",
            fontFamily: "'IBM Plex Mono', monospace", fontSize: "13px",
          },
        }} />
        <LegalPage doc={view} onBack={() => setView(legalReturnView)} />
      </div>
    );
  }

  if (view === "pricing") {
    return (
      <div className="App relative" data-testid="app-root">
        <Toaster position="bottom-right" toastOptions={{
          style: {
            background: "#15171D", border: "1px solid #2A2E38",
            color: "#EDEDF0", borderRadius: "8px",
            fontFamily: "'IBM Plex Mono', monospace", fontSize: "13px",
          },
        }} />
        <PricingPage
          user={user}
          onBack={() => setView(pricingReturnView)}
          onSignIn={handleSignIn}
          onStart={() => setView("build")}
          onLegal={(doc) => openLegal(doc, "pricing")}
        />
      </div>
    );
  }

  return (
    <div className="App relative" data-testid="app-root">
      <Toaster position="bottom-right" toastOptions={{
        style: {
          background: "#15171D", border: "1px solid #2A2E38",
          color: "#EDEDF0", borderRadius: "8px",
          fontFamily: "'IBM Plex Mono', monospace", fontSize: "13px",
        },
      }} />

      <AmbientBackground />

      <main className="relative z-10 max-w-5xl mx-auto p-4 sm:p-6 lg:p-8 space-y-5 sm:space-y-6">
        <TerminalHero status={status} onBack={() => setView("landing")} />

        <div className="flex items-center justify-end gap-2">
          <button
            data-testid="build-pricing-link"
            onClick={() => openPricing("build")}
            className="flex items-center gap-2 border border-line rounded-md text-text2 px-3 py-2 hover:border-text3 transition-colors text-xs"
          >
            pricing
          </button>
          {user ? (
            <div className="flex items-center gap-2" data-testid="auth-user">
              {user.picture && (
                <img src={user.picture} alt={user.name}
                     className="w-6 h-6 rounded-full border border-line" />
              )}
              <span className="text-xs font-mono text-text2 hidden sm:inline">
                {user.name || user.email}
              </span>
              <button
                data-testid="open-profile"
                onClick={() => setProfileOpen(true)}
                className="flex items-center gap-2 border border-line rounded-md text-text2 px-3 py-2 hover:border-text3 transition-colors text-xs"
              >
                <User size={14} /> profile
              </button>
              <button
                data-testid="sign-out"
                onClick={handleSignOut}
                className="flex items-center gap-2 border border-line rounded-md text-text2 px-3 py-2 hover:border-text3 transition-colors text-xs"
              >
                <LogOut size={14} /> sign out
              </button>
            </div>
          ) : (
            !checkingAuth && (
              <button
                data-testid="sign-in"
                onClick={handleSignIn}
                className="flex items-center gap-2 border border-line rounded-md text-text2 px-3 py-2 hover:border-text3 transition-colors text-xs"
              >
                <LogIn size={14} /> sign in with google
              </button>
            )
          )}
          <button
            data-testid="open-settings"
            onClick={() => setSettingsOpen(true)}
            className="flex items-center gap-2 border border-line rounded-md text-text2 px-3 py-2 hover:border-text3 transition-colors text-xs"
          >
            <Settings size={14} /> model settings
          </button>
        </div>

        <EvolveButton
          onEvolve={handleEvolve} busy={busy}
          currentStage={stage} assignments={assignments}
        />

        <ChainViewer
          chain={chain}
          onVerify={handleVerify}
          onPush={handlePush}
          streamBuf={streamBuf}
          advanceLog={advanceLog}
        />

        {chains.length > 0 && (
          <section className="panel p-4" data-testid="chain-history">
            <div className="flex items-center justify-between mb-3">
              <div className="text-text2 text-sm">Prior builds</div>
              <div className="flex gap-1 text-xs">
                <button
                  data-testid="filter-all"
                  onClick={() => setHistoryFilter("all")}
                  className={`px-2.5 py-1 rounded-md border ${historyFilter === "all"
                    ? "border-pink bg-pink/10 text-pink"
                    : "border-line text-text2 hover:border-text3"}`}
                >all</button>
                <button
                  data-testid="filter-verified"
                  onClick={() => setHistoryFilter("verified")}
                  className={`px-2.5 py-1 rounded-md border ${historyFilter === "verified"
                    ? "border-canary bg-canary/10 text-canary"
                    : "border-line text-text2 hover:border-text3"}`}
                >✓ verified</button>
              </div>
            </div>
            <ul className="divide-y divide-line max-h-72 overflow-y-auto">
              {chains
                .filter((c) => historyFilter === "all" || c.verified)
                .map((c) => (
                <li key={c.id}>
                  <button
                    data-testid={`history-chain-${c.id.slice(0, 8)}`}
                    onClick={() => openChain(c.id)}
                    className={`w-full text-left py-2.5 px-2 rounded-md text-xs hover:bg-slate2 transition-colors ${
                      chain?.id === c.id ? "bg-slate2" : ""
                    }`}
                  >
                    <div className="flex justify-between items-center gap-2">
                      <span className="text-text truncate">
                        {c.verified && <span className="text-canary">✓ </span>}
                        {c.target_prompt ? c.target_prompt.slice(0, 60) : c.id.slice(0, 12)}
                      </span>
                      <span className="text-text3 shrink-0">
                        {c.status === "failed" ? "failed" : `${c.generations?.length || 0} gen`}
                      </span>
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          </section>
        )}

        <footer className="text-center text-text3 font-mono text-xs py-6 border-t border-line" data-testid="footer">
          capcode · session {sessionId.slice(0, 8)} · © {new Date().getFullYear()} ·{" "}
          <button data-testid="footer-privacy-link" onClick={() => openLegal("privacy", "build")} className="hover:text-text2 underline">
            privacy
          </button>{" "}
          ·{" "}
          <button data-testid="footer-terms-link" onClick={() => openLegal("terms", "build")} className="hover:text-text2 underline">
            terms
          </button>
        </footer>
      </main>

      <SettingsPanel
        sessionId={sessionId}
        user={user}
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        onSaved={(a) => { setAssignments(a); toast.success("agents saved"); }}
      />

      <ProfilePanel
        user={user}
        open={profileOpen}
        onClose={() => setProfileOpen(false)}
      />
    </div>
  );
}

export default App;
