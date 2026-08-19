import React, { useState } from "react";
import { ArrowRight } from "lucide-react";

const STACK_HINTS = [
  { label: "Auto", value: "" },
  { label: "Python + FastAPI", value: "python-fastapi" },
  { label: "React + Vite", value: "node-vite" },
  { label: "Rust", value: "rust" },
  { label: "Go", value: "go" },
  { label: "WebGL / HTML", value: "webgl" },
];

export const EvolveButton = ({ onEvolve, busy, currentStage }) => {
  const [target, setTarget] = useState("");
  const [stackHint, setStackHint] = useState("");

  const trigger = () => {
    const t = target.trim();
    if (!t) return;
    const full = stackHint ? `${t}\n\nStack: ${stackHint}` : t;
    onEvolve(full);
  };

  return (
    <section className="panel p-6 sm:p-8" data-testid="evolve-panel">
      <div className="space-y-5">
        <div>
          <label className="text-text2 text-sm block mb-2" htmlFor="target-prompt">
            What do you want to build?
          </label>
          <textarea
            id="target-prompt"
            data-testid="target-prompt"
            value={target}
            onChange={(e) => setTarget(e.target.value)}
            disabled={busy}
            rows={4}
            placeholder="e.g. a react app that shows a live BTC price ticker with a spark line chart"
            className="w-full bg-ink border border-line focus:border-pink text-text px-3 py-2.5 rounded-md text-sm resize-y disabled:opacity-40 outline-none placeholder:text-text3"
          />
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <span className="text-text3 text-xs mr-1">Stack</span>
          {STACK_HINTS.map((s) => (
            <button
              key={s.value}
              data-testid={s.value ? `stack-hint-${s.value}` : "stack-hint-auto"}
              disabled={busy}
              onClick={() => setStackHint(s.value)}
              className={`border px-2.5 py-1 rounded-md text-xs transition-colors disabled:opacity-40 ${
                stackHint === s.value
                  ? "border-canary bg-canary/10 text-canary"
                  : "border-line text-text2 hover:border-text3"
              }`}
            >
              {s.label}
            </button>
          ))}
        </div>

        <div className="flex justify-end pt-2">
          <button
            data-testid="evolve-btn"
            onClick={trigger}
            disabled={busy || !target.trim()}
            className="flex items-center gap-2 bg-pink text-white px-6 py-3 rounded-md font-display font-semibold text-sm hover:bg-pink/90 disabled:opacity-40 disabled:cursor-not-allowed transition-colors min-w-[160px] justify-center"
          >
            {busy ? (currentStage || "Building…") : (
              <>Build <ArrowRight size={15} /></>
            )}
          </button>
        </div>
      </div>
    </section>
  );
};
