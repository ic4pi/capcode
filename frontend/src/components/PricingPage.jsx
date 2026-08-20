import React, { useEffect, useState } from "react";
import { Check, ArrowLeft, Zap } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { AmbientBackground } from "@/components/AmbientBackground";

const Feature = ({ children, dim }) => (
  <li className={`flex items-start gap-2 text-sm ${dim ? "text-text3" : "text-text2"}`}>
    <Check size={15} className={`mt-0.5 shrink-0 ${dim ? "text-text3" : "text-canary"}`} />
    <span>{children}</span>
  </li>
);

const DEFAULT_PRICES = {
  trial: { amount: 2.99, currency: "usd", interval: "one_time" },
  paid: { amount: 16.00, currency: "usd", interval: "month" },
  paid_annual: { amount: 192.00, currency: "usd", interval: "year" },
};

const fmt = (amount) => (Number.isInteger(amount) ? amount.toFixed(0) : amount.toFixed(2));

export const PricingPage = ({ user, onBack, onSignIn, onStart, onLegal }) => {
  const [busy, setBusy] = useState(null); // "trial" | "paid" | null
  const [annual, setAnnual] = useState(false);
  const [prices, setPrices] = useState(DEFAULT_PRICES);

  useEffect(() => {
    api.tierPrices().then((p) => setPrices((prev) => ({ ...prev, ...p }))).catch(() => {});
  }, []);

  const handleTrial = async () => {
    if (!user) { onSignIn(); return; }
    setBusy("trial");
    try {
      const res = await api.checkout("trial");
      if (res.checkout_url) {
        window.location.href = res.checkout_url;
        return;
      }
      toast.success(`$${fmt(prices.trial.amount)} TRIAL STARTED — full NVIDIA-only 6-role team unlocked for 7 days`);
      onStart();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "couldn't start trial");
    } finally {
      setBusy(null);
    }
  };

  const handleUpgrade = async () => {
    if (!user) { onSignIn(); return; }
    const plan = annual ? "paid_annual" : "paid";
    setBusy("paid");
    try {
      const res = await api.checkout(plan);
      if (res.checkout_url) {
        window.location.href = res.checkout_url;
      } else {
        toast.success("UPGRADED — CREDIT CYCLE OPEN");
        onStart();
      }
    } catch (e) {
      toast.error(e?.response?.data?.detail || "checkout failed");
    } finally {
      setBusy(null);
    }
  };

  const paidPrice = annual ? prices.paid_annual : prices.paid;

  return (
    <div className="min-h-screen flex flex-col relative overflow-hidden" data-testid="pricing-page">
      <AmbientBackground />

      <header className="relative z-10 max-w-5xl mx-auto w-full px-6 pt-8">
        <button
          data-testid="pricing-back"
          onClick={onBack}
          className="flex items-center gap-2 text-text2 hover:text-text text-sm font-mono transition-colors"
        >
          <ArrowLeft size={14} /> capcode
        </button>
      </header>

      <main className="flex-1 relative z-10">
        <div className="max-w-5xl mx-auto px-6 py-14 w-full">
          <div className="label-xs text-canary mb-4">pricing</div>
          <h1 className="font-display font-extrabold text-4xl sm:text-5xl leading-[1.05] text-text max-w-2xl">
            Two models is free.<br />Six is ${fmt(prices.paid.amount)}/mo.
          </h1>
          <p className="mt-5 text-text2 text-base sm:text-lg leading-relaxed max-w-xl">
            Every plan runs the same checkpointed pipeline. Paid adds two more
            bots to the chain and a credit ledger that pays for its own
            self-correction loop.
          </p>

          <div className="mt-8 grid grid-cols-1 md:grid-cols-3 gap-5">
            {/* FREE */}
            <div className="rounded-lg border border-line bg-slate p-6 flex flex-col" data-testid="plan-free">
              <div className="font-mono text-xs text-text3 uppercase tracking-wide">free</div>
              <div className="mt-2 font-display font-bold text-3xl text-text">$0</div>
              <div className="text-text3 text-xs font-mono mt-1">forever</div>
              <ul className="mt-6 space-y-3 flex-1">
                <Feature>Teacher + Artist — 2-bot chain</Feature>
                <Feature>Runs, self-corrects once, gets scored</Feature>
                <Feature>Download or push finished code</Feature>
                <Feature dim>No Architect or Reviewer pass</Feature>
                <Feature dim>Shared daily capacity pool</Feature>
              </ul>
              <button
                data-testid="plan-free-cta"
                onClick={onStart}
                className="mt-6 w-full border border-line rounded-md py-2.5 text-sm font-display font-semibold text-text2 hover:border-text3 hover:text-text transition-colors"
              >
                Start building
              </button>
            </div>

            {/* TRIAL */}
            <div className="rounded-lg border border-canary/40 bg-slate p-6 flex flex-col relative" data-testid="plan-trial">
              <div className="absolute -top-3 left-6 px-2 py-0.5 rounded-full bg-canary text-ink text-[10px] font-mono font-semibold tracking-wide">
                COVERS ITS OWN COST
              </div>
              <div className="font-mono text-xs text-text3 uppercase tracking-wide">7-day trial</div>
              <div className="mt-2 flex items-baseline gap-1">
                <span className="font-display font-bold text-3xl text-text">${fmt(prices.trial.amount)}</span>
                <span className="text-text3 text-sm font-mono">one-time</span>
              </div>
              <div className="text-text3 text-xs font-mono mt-1">not a subscription — charged once</div>
              <ul className="mt-6 space-y-3 flex-1">
                <Feature>Full 6-role team — Architect + Reviewer added</Feature>
                <Feature>NVIDIA-only models — priced to exactly cover this
                  trial's own recursion-loop compute cost</Feature>
                <Feature>Own token pool, not shared with free users</Feature>
                <Feature>Higher structural quality on every build</Feature>
                <Feature dim>Expires after 7 days or budget, whichever first</Feature>
              </ul>
              <button
                data-testid="plan-trial-cta"
                onClick={handleTrial}
                disabled={busy === "trial"}
                className="mt-6 w-full border border-canary text-canary rounded-md py-2.5 text-sm font-display font-semibold hover:bg-canary hover:text-ink transition-colors disabled:opacity-50"
              >
                {busy === "trial" ? "…" : user ? `Start trial — $${fmt(prices.trial.amount)}` : "Sign in to start trial"}
              </button>
            </div>

            {/* PAID */}
            <div className="rounded-lg border-2 border-pink bg-slate p-6 flex flex-col relative" data-testid="plan-paid">
              <div className="absolute -top-3 left-6 px-2 py-0.5 rounded-full bg-pink text-white text-[10px] font-mono font-semibold tracking-wide">
                RECOMMENDED
              </div>
              <div className="font-mono text-xs text-text3 uppercase tracking-wide">paid</div>
              <div className="mt-3 flex items-center gap-2" data-testid="billing-toggle">
                <button
                  data-testid="billing-toggle-monthly"
                  onClick={() => setAnnual(false)}
                  className={`px-2.5 py-1 rounded-md text-[11px] font-mono border transition-colors ${
                    !annual ? "border-pink bg-pink/10 text-pink" : "border-line text-text2 hover:border-text3"}`}
                >
                  monthly
                </button>
                <button
                  data-testid="billing-toggle-annual"
                  onClick={() => setAnnual(true)}
                  className={`px-2.5 py-1 rounded-md text-[11px] font-mono border transition-colors ${
                    annual ? "border-pink bg-pink/10 text-pink" : "border-line text-text2 hover:border-text3"}`}
                >
                  annual
                </button>
              </div>
              <div className="mt-2 flex items-baseline gap-1">
                <span className="font-display font-bold text-3xl text-text" data-testid="plan-paid-amount">
                  ${fmt(paidPrice.amount)}
                </span>
                <span className="text-text3 text-sm font-mono">{annual ? "/yr" : "/mo"}</span>
              </div>
              <div className="text-text3 text-xs font-mono mt-1">cancel anytime</div>
              <ul className="mt-6 space-y-3 flex-1">
                <Feature>Full 6-role team — Architect + Reviewer added</Feature>
                <Feature>$8 API budget/mo — covers your own builds</Feature>
                <Feature>$4 recursion fund — pays for the automatic
                  refinement loop when a build scores under 80</Feature>
                <Feature>Credit Rebirth — the loop's cost is refunded when
                  it produces a genuinely better build</Feature>
                {annual && <Feature>Credits still refill every 30 days, same as monthly</Feature>}
              </ul>
              <button
                data-testid="plan-paid-cta"
                onClick={handleUpgrade}
                disabled={busy === "paid"}
                className="mt-6 w-full flex items-center justify-center gap-2 bg-pink text-white rounded-md py-2.5 text-sm font-display font-semibold hover:bg-pink/90 transition-colors disabled:opacity-50"
              >
                <Zap size={14} /> {busy === "paid" ? "…" : user ? `Upgrade — $${fmt(paidPrice.amount)}${annual ? "/yr" : "/mo"}` : "Sign in to upgrade"}
              </button>
            </div>
          </div>

          <div className="mt-10 pt-6 border-t border-line text-xs text-text3 max-w-2xl leading-relaxed font-mono">
            The trial's ${fmt(prices.trial.amount)} is a one-time charge, not a subscription — it's
            priced to exactly cover the NVIDIA compute cost of a full 6-role
            trial build plus its automatic refinement pass, so the trial never
            runs at a loss. "Recursion fund" and "Credit Rebirth" describe how
            the paid plan spends and recoups your subscription internally —
            $8 covers your build calls, $4 is reserved to pay for one
            refinement pass per build, and that $4 is credited back to your
            budget whenever the refinement pass produces a Knowledge Module
            worth keeping for future builds. Annual billing doesn't change any
            of that — it's the same monthly credit refill, just paid once a year.
          </div>
        </div>
      </main>

      <footer className="text-text3 font-mono text-xs px-6 py-6 border-t border-line relative z-10 flex items-center gap-3">
        <span>capcode</span>
        <button data-testid="pricing-privacy-link" onClick={() => onLegal?.("privacy")} className="hover:text-text2 underline">
          privacy
        </button>
        <button data-testid="pricing-terms-link" onClick={() => onLegal?.("terms")} className="hover:text-text2 underline">
          terms
        </button>
      </footer>
    </div>
  );
};
