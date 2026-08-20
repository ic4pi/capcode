import React, { useState } from "react";
import { AlertTriangle, X } from "lucide-react";

/**
 * Uncensored-mode consent waiver.
 *
 * Rendered when the user toggles uncensored/Venice mode ON.
 * The parent MUST NOT flip the toggle until `onAgree` resolves — this
 * component itself calls `onAgree()` only after the user explicitly clicks
 * "I agree". Closing the modal (X or backdrop) resolves nothing and leaves
 * the toggle off.
 */
export const VeniceConsentModal = ({ open, onAgree, onCancel, chainId = null }) => {
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState(null);

  if (!open) return null;

  const handleAgree = async () => {
    if (submitting) return;
    setErr(null);
    setSubmitting(true);
    try {
      await onAgree?.(chainId);
    } catch (e) {
      setErr(e?.response?.data?.detail || e?.message || "consent record failed");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-[200] bg-black/90 flex items-center justify-center p-4"
      data-testid="venice-consent-modal"
      onClick={(e) => e.target === e.currentTarget && !submitting && onCancel?.()}
    >
      <div
        className="panel w-full max-w-lg"
        style={{ borderColor: "rgba(255,64,192,0.7)" }}
      >
        <div className="flex items-center justify-between border-b border-neon_magenta/40 p-4">
          <div className="flex items-center gap-2">
            <AlertTriangle size={16} className="text-neon_magenta" />
            <div className="label-xs text-neon_magenta neon-magenta">
              [ CAPCODE :: UNCENSORED WAIVER ]
            </div>
          </div>
          <button
            data-testid="venice-consent-close"
            onClick={onCancel}
            disabled={submitting}
            className="border border-phosphor/40 text-phosphor p-2 hover:bg-phosphor hover:text-black disabled:opacity-30"
          >
            <X size={14} />
          </button>
        </div>

        <div className="p-5 space-y-4 font-mono text-xs text-phosphor2">
          <div className="font-bbs text-lg text-neon_magenta neon-magenta uppercase tracking-widest">
            you are entering uncensored mode
          </div>
          <p>
            By enabling uncensored / Venice-routed generation, you accept that:
          </p>
          <ul className="list-disc pl-5 space-y-2 text-phosphor2">
            <li>
              <span className="text-phosphor">You are solely responsible</span> for
              any code the AI generates in uncensored mode, and for everything
              that code subsequently produces, enables, or is used for —
              legally, ethically, and operationally.
            </li>
            <li>
              CapCode, its operators, and every upstream provider accept
              <span className="text-neon_magenta neon-magenta"> no responsibility </span>
              under any circumstances for the outputs of uncensored builds, their
              downstream effects, or any harm caused by their use.
            </li>
            <li>
              This waiver is recorded per acceptance. Toggling uncensored mode on
              again — even seconds later — requires a new acceptance.
            </li>
          </ul>

          {err && (
            <div
              data-testid="venice-consent-error"
              className="border border-red-500/60 bg-red-500/10 text-red-300 p-2 text-[11px]"
            >
              {err}
            </div>
          )}
        </div>

        <div className="border-t border-neon_magenta/30 p-4 flex items-center justify-end gap-2">
          <button
            data-testid="venice-consent-cancel"
            onClick={onCancel}
            disabled={submitting}
            className="border border-phosphor/40 text-phosphor2 px-3 py-2 label-xs hover:bg-phosphor hover:text-black disabled:opacity-30"
          >
            cancel
          </button>
          <button
            data-testid="venice-consent-agree"
            onClick={handleAgree}
            disabled={submitting}
            className="border border-neon_magenta bg-neon_magenta/15 text-neon_magenta px-4 py-2 label-xs hover:bg-neon_magenta hover:text-black transition-colors disabled:opacity-30 neon-magenta"
          >
            {submitting ? "recording…" : "i agree"}
          </button>
        </div>
      </div>
    </div>
  );
};
