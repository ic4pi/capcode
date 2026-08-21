import React, { useEffect, useState } from "react";
import { X, User, CreditCard } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";

const TOPUP_PACKAGES = [
  { id: "8", usd: 8, credits: 500 },
  { id: "16", usd: 16, credits: 1000 },
  { id: "32", usd: 32, credits: 2000 },
];

export const ProfilePanel = ({ user, open, onClose }) => {
  const [profile, setProfile] = useState(null);
  const [loading, setLoading] = useState(false);
  const [buyingId, setBuyingId] = useState(null);

  useEffect(() => {
    if (!open || !user) return;
    setLoading(true);
    api.getProfile().then(setProfile).catch(() => setProfile(null)).finally(() => setLoading(false));
  }, [open, user]);

  if (!open) return null;

  const handleTopup = async (pkgId) => {
    setBuyingId(pkgId);
    try {
      const res = await api.topupCheckout(pkgId);
      if (res.checkout_url) {
        window.location.href = res.checkout_url;
        return;
      }
      toast.success(`+${res.credits_added} CREDITS ADDED`);
      const p = await api.getProfile();
      setProfile(p);
    } catch (e) {
      toast.error(e?.response?.data?.detail || "top-up failed");
    } finally {
      setBuyingId(null);
    }
  };

  const tierLabel = () => {
    if (!profile) return "—";
    if (profile.tier === "trial") return `TRIAL — ${profile.trial_days_remaining ?? "?"} DAYS LEFT`;
    if (profile.tier === "paid") return `PAID — ${profile.billing_interval === "annual" ? "ANNUAL" : "MONTHLY"}`;
    return "FREE";
  };

  return (
    <div
      className="fixed inset-0 z-[100] bg-black/85 flex items-start justify-center p-2 sm:p-6"
      data-testid="profile-panel"
      onClick={(e) => e.target === e.currentTarget && onClose?.()}
    >
      <div
        className="panel w-full max-w-lg max-h-[92vh] flex flex-col overflow-y-auto"
        style={{ borderColor: "rgba(0,255,255,0.5)" }}
      >
        <div className="flex items-center justify-between border-b border-phosphor/30 p-4">
          <div className="flex items-center gap-2">
            <User size={16} className="text-neon_cyan" />
            <div className="label-xs text-neon_cyan neon-cyan">[ CAPCODE :: PROFILE ]</div>
          </div>
          <button
            data-testid="profile-close"
            onClick={onClose}
            className="border border-phosphor/40 text-phosphor p-2 hover:bg-phosphor hover:text-black"
          >
            <X size={14} />
          </button>
        </div>

        {!user ? (
          <div className="p-6 text-phosphor3 label-xs">sign in with google to view your profile</div>
        ) : loading || !profile ? (
          <div className="p-6 text-phosphor3 font-mono text-sm">loading…</div>
        ) : (
          <div className="p-5 space-y-5">
            <div className="flex items-center gap-3">
              {profile.picture && (
                <img
                  src={profile.picture}
                  alt={profile.name}
                  className="w-10 h-10 rounded-full border border-phosphor/40"
                />
              )}
              <div>
                <div className="text-phosphor font-mono text-sm" data-testid="profile-name">
                  {profile.name}
                </div>
                <div className="text-phosphor3 font-mono text-xs" data-testid="profile-email">
                  {profile.email}
                </div>
              </div>
            </div>

            <div className="border border-phosphor/30 p-3 space-y-2">
              <div className="label-xs text-phosphor3">subscription status</div>
              <div className="font-bbs text-lg text-neon_magenta neon-magenta" data-testid="profile-tier">
                {tierLabel()}
              </div>
              {!profile.subscribed && (
                <button
                  data-testid="profile-see-plans"
                  onClick={() => window.dispatchEvent(new CustomEvent("capcode:open-pricing"))}
                  className="label-xs text-neon_cyan underline hover:text-phosphor"
                >
                  see plans →
                </button>
              )}
            </div>

            <div className="border border-phosphor/30 p-3 space-y-2" data-testid="profile-credits">
              <div className="label-xs text-phosphor3">credits left</div>
              {profile.subscribed && profile.cycle ? (
                <>
                  <div className="font-mono text-xs text-phosphor2">
                    api budget: {profile.cycle.api_budget_credits} credits
                  </div>
                  <div className="font-mono text-xs text-phosphor2">
                    recursion fund: {profile.cycle.recursion_fund_credits} credits
                  </div>
                </>
              ) : (
                <div className="font-mono text-xs text-phosphor3">no active subscription</div>
              )}
              <div className="font-mono text-xs text-neon_yellow" data-testid="profile-topup-balance">
                top-up balance: {profile.topup_credits_balance || 0} credits
              </div>
            </div>

            <div className="space-y-2">
              <div className="label-xs text-neon_cyan neon-cyan flex items-center gap-1">
                <CreditCard size={12} /> top up credits
              </div>
              <div className="grid grid-cols-3 gap-2">
                {TOPUP_PACKAGES.map((p) => (
                  <button
                    key={p.id}
                    data-testid={`topup-${p.id}`}
                    onClick={() => handleTopup(p.id)}
                    disabled={buyingId === p.id}
                    className="border border-neon_yellow text-neon_yellow py-3 px-2 text-center hover:bg-neon_yellow hover:text-black transition-colors disabled:opacity-50"
                  >
                    <div className="font-display font-bold text-lg">${p.usd}</div>
                    <div className="text-[10px] font-mono mt-1">{p.credits} credits</div>
                  </button>
                ))}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
